#!/usr/bin/env python3
import os
import sys
import re
import json
import time
import sqlite3
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone

# Path Configuration
INSTALL_DIR       = Path("/home/pi/sre")
DB_PATH           = INSTALL_DIR / "sre_state.db"
DAEMON_LOG        = INSTALL_DIR / "daemon.log"
JAN_LOG           = INSTALL_DIR / "jan_developer.log"

# Fallbacks for local MacBook testing
if not INSTALL_DIR.exists():
    INSTALL_DIR   = Path(".").resolve()
    DB_PATH       = INSTALL_DIR / "sre_state.db"
    DAEMON_LOG    = INSTALL_DIR / "daemon.log"
    JAN_LOG       = INSTALL_DIR / "jan_developer.log"

def log_info(msg):
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = f"{timestamp} [INFO] Jan: {msg}\n"
    print(log_line.strip())
    with open(JAN_LOG, "a") as f:
        f.write(log_line)

def log_error(msg):
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = f"{timestamp} [ERROR] Jan: {msg}\n"
    print(log_line.strip())
    with open(JAN_LOG, "a") as f:
        f.write(log_line)

def load_env():
    env_file = INSTALL_DIR / ".env"
    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("'").strip('"')
        log_info("Loaded environment variables from .env file.")

# Load env immediately
load_env()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_text(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        log_error(f"Telegram notification failed: {e}")

def query_gemini(prompt):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
    try:
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        log_error(f"Gemini API failure: {e}")
        return None

def query_groq(prompt):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    try:
        r = requests.post(url, json=data, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log_error(f"Groq API failure: {e}")
        return None

def query_claude(prompt):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    data = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        r = requests.post(url, json=data, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except Exception as e:
        log_error(f"Claude API failure: {e}")
        return None

def query_llm_cascade(prompt):
    res = query_gemini(prompt)
    if res: return res, "gemini"
    res = query_groq(prompt)
    if res: return res, "groq"
    res = query_claude(prompt)
    if res: return res, "claude"
    return None, None

def check_for_tracebacks():
    """Scans journalctl for unhandled Python tracebacks in the last 5 minutes."""
    errors = []
    try:
        res = subprocess.run(
            ["journalctl", "-u", "sre-daemon", "-p", "err", "--since", "5 minutes ago", "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0 and res.stdout.strip():
            content = res.stdout.strip()
            if "Traceback (most recent call last)" in content:
                errors.append({"source": "journalctl", "content": content})
    except Exception as e:
        log_error(f"Failed to scan journalctl: {e}")

    return errors

def check_failed_heal_history():
    """Checks sqlite DB for recently failed healing executions."""
    if not DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT error_message, project_tag, llm_prompt_used, actions_json, execution_output, created_at 
                FROM heal_history 
                WHERE success = 0 
                ORDER BY id DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                data = {
                    "error_message": row["error_message"],
                    "project_tag": row["project_tag"],
                    "prompt": row["llm_prompt_used"],
                    "actions": row["actions_json"],
                    "execution_output": row["execution_output"],
                    "created_at": row["created_at"]
                }
                # Parse timestamp
                created_str = data["created_at"]
                if "+" in created_str:
                    created_str = created_str.split("+")[0]
                created_dt = datetime.fromisoformat(created_str).replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - created_dt).total_seconds()
                # If the failed action is fresh (last 5 minutes)
                if age < 300:
                    return data
    except Exception as e:
        log_error(f"Failed to query heal_history DB: {e}")
    return None

def analyze_and_patch(error_context, failed_history=None):
    """Asks the LLM to write a patch for sre_daemon.py based on the failure context."""
    log_info("Starting self-evolution analysis...")

    # Load current sre_daemon.py source code to provide context
    sre_daemon_path = INSTALL_DIR / "sre_daemon.py"
    if not sre_daemon_path.exists():
        log_error("sre_daemon.py not found, aborting.")
        return False

    with open(sre_daemon_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    # Formulate prompt
    prompt = f"""You are Jan, the self-evolving AI SRE developer agent.
Your primary task is to fix a bug in 'sre_daemon.py' based on the failure context below.

[FAILURE CONTEXT]
{error_context}
"""

    if failed_history:
        prompt += f"""
[FAILED HEALING ACTION DETAILS]
- Service: {failed_history.get('project_tag')}
- Error Snippet: {failed_history.get('error_message')}
- Proposed Action: {failed_history.get('actions')}
- Execution Output: {failed_history.get('execution_output')}
"""

    prompt += """
Please analyze 'sre_daemon.py' and produce a search-and-replace patch block to resolve this bug.
Your patch must be structurally sound and completely fix the root cause (e.g., path resolution issues, type errors, NameErrors).

Output your response strictly as a JSON object of this format (do not include markdown wrapper blocks or explanations):
{
  "patches": [
    {
      "search": "exact code lines to replace",
      "replace": "new corrected code lines"
    }
  ]
}
"""

    response_text, provider = query_llm_cascade(prompt)
    if not response_text:
        log_error("Could not obtain a response from any LLM in the cascade.")
        return False

    log_info(f"LLM cascade response received from {provider}.")

    # Clean JSON markers if present
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        patch_data = json.loads(cleaned)
        patches = patch_data.get("patches", [])
        if not patches:
            log_error("No patches found in LLM response.")
            return False

        # Apply patches atomically
        applied_count = 0
        for p in patches:
            search_str = p.get("search")
            replace_str = p.get("replace")
            if not search_str or replace_str is None:
                continue

            current_content = sre_daemon_path.read_text(encoding="utf-8")
            count = current_content.count(search_str)
            if count == 0:
                log_error(f"Search block not found in file:\n{search_str[:150]}")
                continue
            if count > 1:
                log_error(f"Search block matches multiple locations ({count}), patch is ambiguous.")
                continue

            new_content = current_content.replace(search_str, replace_str, 1)
            sre_daemon_path.write_text(new_content, encoding="utf-8")
            applied_count += 1
            log_info(f"Applied patch to {sre_daemon_path}.")

        if applied_count == 0:
            log_error("No patches could be applied successfully.")
            return False

        # Verification Gate
        log_info("Running pytest suite for validation...")
        pytest_cmd = [str(INSTALL_DIR / "venv/bin/pytest"), "-v"]
        if not (INSTALL_DIR / "venv").exists():
            pytest_cmd = ["pytest", "-v"] # fallback
            
        test_res = subprocess.run(pytest_cmd, cwd=str(INSTALL_DIR), capture_output=True, text=True, timeout=60)
        if test_res.returncode == 0:
            log_info("Verification gate PASSED! All unit tests pass.")
            
            # Commit and push changes
            try:
                subprocess.run(["git", "-C", str(INSTALL_DIR), "add", "sre_daemon.py"], check=True)
                subprocess.run(["git", "-C", str(INSTALL_DIR), "commit", "-m", "jan: otonom kod iyilestirmesi ve hata giderimi"], check=True)
                push_res = subprocess.run(["git", "-C", str(INSTALL_DIR), "push", "origin", "main"], capture_output=True, text=True, timeout=30)
                if push_res.returncode == 0:
                    log_info("Successfully pushed changes to GitHub.")
                else:
                    log_error(f"Git push failed: {push_res.stderr}")
            except Exception as git_ex:
                log_error(f"Git operations failed: {git_ex}")

            # Restart SRE Daemon service
            log_info("Restarting SRE Daemon service...")
            restart_res = subprocess.run(["sudo", "/usr/bin/systemctl", "restart", "sre-daemon"], capture_output=True, text=True, timeout=10)
            if restart_res.returncode == 0:
                log_info("SRE Daemon service restarted successfully.")
            else:
                log_error(f"Failed to restart SRE Daemon: {restart_res.stderr}")

            # Notify via Telegram
            send_telegram_text(
                "🤖 *Jan: Otonom Gelişim Başarılı!*\n\n"
                f"SRE Daemon üzerinde tespit edilen hata giderildi.\n"
                f"⏱ *Doğrulama*: `pytest` yeşil ✅\n"
                f"📦 *Push*: GitHub'a gönderildi\n"
                f"🔄 *Servis*: Yeniden başlatıldı."
            )
            return True
        else:
            log_error(f"Verification gate FAILED! Pytest output:\n{test_res.stdout}\n{test_res.stderr}")
            # Rollback
            subprocess.run(["git", "-C", str(INSTALL_DIR), "checkout", "--", "sre_daemon.py"], check=True)
            log_info("Git checkout executed. Rollback successful.")
            
            send_telegram_text(
                "❌ *Jan: Otonom Gelişim Başarısız*\n\n"
                "Üretilen çözüm testleri geçemedi, değişiklikler geri alındı.\n"
                f"Hata: `{md_escape(test_res.stderr[:200] or test_res.stdout[:200])}`"
            )
            return False

    except Exception as ex:
        log_error(f"An error occurred during patching: {ex}")
        # Rollback as last resort
        try:
            subprocess.run(["git", "-C", str(INSTALL_DIR), "checkout", "--", "sre_daemon.py"], check=True)
        except Exception:
            pass
        return False

def md_escape(text: str) -> str:
    for char in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(char, f"\\{char}")
    return text

def main():
    log_info("Jan Developer Agent started checking logs and db...")
    
    # Check 1: Failed healing in DB
    failed_history = check_failed_heal_history()
    if failed_history:
        err_msg = f"Failed heal action logged in history: {failed_history.get('error_message')}"
        log_info(err_msg)
        analyze_and_patch(err_msg, failed_history)
        return

    # Check 2: Tracebacks in log files
    tracebacks = check_for_tracebacks()
    if tracebacks:
        # Check if the traceback is fresh
        tb = tracebacks[-1]
        log_info(f"Traceback detected in {tb['source']}.")
        analyze_and_patch(tb["content"])
        return

    log_info("No fresh tracebacks or failed actions detected. Going to sleep.")

if __name__ == "__main__":
    main()

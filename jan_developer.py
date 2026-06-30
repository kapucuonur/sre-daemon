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
import xml.etree.ElementTree as ET

def parse_xml_patches(xml_text):
    """
    Parses XML code modification patches, returning a list of patch dicts.
    Uses standard ET parsing and falls back to regex in case of minor XML structure issues.
    """
    cleaned = xml_text.strip()
    if cleaned.startswith("```xml"):
        cleaned = cleaned[6:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    if not cleaned.startswith("<patches>"):
        if cleaned.startswith("<patch>"):
            cleaned = f"<patches>{cleaned}</patches>"
        else:
            p_idx = cleaned.find("<patch>")
            if p_idx != -1:
                cleaned = f"<patches>{cleaned[p_idx:]}"
                if not cleaned.endswith("</patches>"):
                    cleaned += "</patches>"

    patches = []
    try:
        root = ET.fromstring(cleaned)
        for patch_node in root.findall("patch"):
            target_node = patch_node.find("target")
            target = target_node.text.strip() if target_node is not None and target_node.text else ""
            
            search_node = patch_node.find("search")
            search_val = search_node.text if search_node is not None and search_node.text else ""
            
            replace_node = patch_node.find("replace")
            replace_val = replace_node.text if replace_node is not None and replace_node.text else ""
            
            patches.append({
                "target": target,
                "search": search_val,
                "replace": replace_val
            })
    except Exception as e:
        log_error(f"XML Standard Parser failed ({e}), falling back to Regex parser.")
        matches = re.findall(r'<patch>\s*<target>(.*?)</target>\s*<search>\s*<!\[CDATA\[(.*?)]]>\s*</search>\s*<replace>\s*<!\[CDATA\[(.*?)]]>\s*</replace>\s*</patch>', cleaned, re.DOTALL)
        for m in matches:
            patches.append({
                "target": m[0].strip(),
                "search": m[1],
                "replace": m[2]
            })
            
    return patches

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
                project_tag = row["project_tag"] or ""
                error_message = row["error_message"] or ""
                if "jan" in project_tag.lower() or "jan" in error_message.lower():
                    return None
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

def extract_relevant_context(error_context, source_code):
    lines = source_code.splitlines()
    matches = re.findall(r'sre_daemon\.py", line (\d+)', error_context)
    if not matches:
        matches = re.findall(r'line (\d+)', error_context)
    
    if not matches:
        return "No specific code lines could be resolved from the traceback context."
        
    extracted_sections = []
    for line_str in set(matches):
        try:
            line_no = int(line_str)
            start = max(0, line_no - 50)
            end = min(len(lines), line_no + 50)
            segment = "\n".join(f"{idx+1}: {lines[idx]}" for idx in range(start, end))
            extracted_sections.append(
                f"--- Code surrounding line {line_no} in sre_daemon.py ---\n"
                f"{segment}\n"
            )
        except Exception:
            pass
            
    if not extracted_sections:
        return "No specific code lines could be resolved from the traceback context."
        
    return "\n".join(extracted_sections)

def self_heal_patch_indentation(search_str, replace_str, current_content, target_name):
    """
    Attempts to self-heal indentation mismatches between the search block and the target file.
    If a unique match is found by ignoring leading indentation, re-aligns the patch's indentation.
    """
    count = current_content.count(search_str)
    if count > 0:
        return search_str, replace_str, count

    # Try matching by stripping leading whitespace on each line
    search_lines = [l.strip() for l in search_str.splitlines() if l.strip()]
    if not search_lines:
        return search_str, replace_str, count

    file_lines = current_content.splitlines()
    matched_indices = []
    for idx, f_line in enumerate(file_lines):
        if f_line.strip() == search_lines[0]:
            # Verify if subsequent lines match when stripped
            match_ok = True
            for s_idx, s_line in enumerate(search_lines):
                if idx + s_idx >= len(file_lines) or file_lines[idx + s_idx].strip() != s_line:
                    match_ok = False
                    break
            if match_ok:
                matched_indices.append(idx)
    
    if len(matched_indices) == 1:
        # Found exactly one unique match!
        start_idx = matched_indices[0]
        first_line = file_lines[start_idx]
        actual_indent = len(first_line) - len(first_line.lstrip())
        
        # Find the indentation of the first line of LLM's search block
        llm_first_line = ""
        for sl in search_str.splitlines():
            if sl.strip():
                llm_first_line = sl
                break
        
        if llm_first_line:
            llm_indent = len(llm_first_line) - len(llm_first_line.lstrip())
            indent_diff = actual_indent - llm_indent
            
            # Re-indent search_str and replace_str by indent_diff
            new_search_lines = []
            for sl in search_str.splitlines():
                if sl.strip():
                    if indent_diff > 0:
                        new_search_lines.append(" " * indent_diff + sl)
                    else:
                        new_search_lines.append(sl[-indent_diff:])
                else:
                    new_search_lines.append(sl)
            search_str = "\n".join(new_search_lines)

            new_replace_lines = []
            for rl in replace_str.splitlines():
                if rl.strip():
                    if indent_diff > 0:
                        new_replace_lines.append(" " * indent_diff + rl)
                    else:
                        leading = len(rl) - len(rl.lstrip())
                        strip_len = min(leading, -indent_diff)
                        new_replace_lines.append(rl[strip_len:])
                else:
                    new_replace_lines.append(rl)
            replace_str = "\n".join(new_replace_lines)
            
            log_info(f"Self-healed patch indentation for {target_name} (indent diff: {indent_diff}).")
            count = current_content.count(search_str)

    return search_str, replace_str, count

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

    code_context = extract_relevant_context(error_context, source_code)

    # Formulate prompt
    prompt = f"""You are Jan, the self-evolving AI SRE developer agent.
Your primary task is to fix a bug in 'sre_daemon.py' based on the failure context and code context below.

[FAILURE CONTEXT]
{error_context}

[CODE CONTEXT FROM SRE_DAEMON.PY]
{code_context}
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
Please analyze the code context, identify the root cause of the failure, and produce a search-and-replace patch block to resolve the bug in 'sre_daemon.py'.
Make sure your search block matches the code context EXACTLY (including line indentation). Do not include line numbers in the search or replace blocks.

Output your response strictly as an XML document using CDATA blocks for search and replace code segments, like this:
<patches>
  <patch>
    <target>sre_daemon.py</target>
    <search><![CDATA[
def get_daemon_setting(key: str, default: str = "") -> str:
    ]]></search>
    <replace><![CDATA[
def get_daemon_setting(key: str, default: str = "") -> str:
    # new corrected logic here
    ]]></replace>
  </patch>
</patches>
"""

    response_text, provider = query_llm_cascade(prompt)
    if not response_text:
        log_error("Could not obtain a response from any LLM in the cascade.")
        return False

    log_info(f"LLM cascade response received from {provider}.")
    try:
        with open(INSTALL_DIR / "jan_last_patch_response.log", "w") as rf:
            rf.write(response_text)
    except Exception:
        pass



    patches = parse_xml_patches(response_text)
    if not patches:
        log_error("No valid XML patches could be parsed.")
        with open(INSTALL_DIR / "jan_raw_response.txt", "w") as rf:
            rf.write(response_text)
        return False

    try:
        # Apply patches atomically
        applied_count = 0
        for p in patches:
            search_str = p.get("search")
            replace_str = p.get("replace")
            if not search_str or replace_str is None:
                continue

            current_content = sre_daemon_path.read_text(encoding="utf-8")
            search_str, replace_str, count = self_heal_patch_indentation(search_str, replace_str, current_content, "sre_daemon.py")
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

def extract_feature_context(source_code, feature_title, feature_desc):
    """
    Scans the source code for class/method definitions matching keywords
    derived from the feature request and extracts their full bodies.
    """
    lines = source_code.splitlines()
    skeleton = []
    # 1. Build outline
    for idx, line in enumerate(lines):
        if line.startswith("class ") or line.startswith("def ") or line.startswith("    def "):
            if not any(x in line for x in ["__init__", "self.", "return"]):
                skeleton.append(f"Line {idx+1}: {line.strip()}")

    # 2. Derive keywords from title and desc
    target_terms = []
    for word in re.findall(r'\b[a-zA-Z]{5,}\b', f"{feature_title} {feature_desc}"):
        w = word.lower()
        if w in ["scaling", "predictive", "trend", "projected", "anomaly", "stats", "history"]:
            target_terms.append(w)
    
    target_terms = list(set(target_terms))
    if not target_terms:
        target_terms = ["metric", "stats"]

    extracted_blocks = []
    # 3. Extract blocks matching keywords
    for keyword in target_terms:
        for idx, line in enumerate(lines):
            # If function or class definition matches keyword
            if (line.startswith("class ") or "def " in line) and keyword in line.lower():
                start = idx
                indent = len(line) - len(line.lstrip())
                end = min(len(lines), idx + 80)
                # Find actual end based on indentation
                for next_idx in range(idx + 1, min(len(lines), idx + 100)):
                    next_line = lines[next_idx]
                    if next_line.strip():
                        next_indent = len(next_line) - len(next_line.lstrip())
                        if next_indent <= indent and not next_line.strip().startswith("#"):
                            end = next_idx
                            break
                segment = "\n".join(lines[start:end])
                block_title = f"--- Definition matching '{keyword}' near line {start+1} ---"
                # Avoid duplicate extractions of the exact same block
                if not any(block_title in b for b in extracted_blocks):
                    extracted_blocks.append(f"{block_title}\n{segment}\n")

    return "--- CODE SKELETON ---\n" + "\n".join(skeleton[:50]) + "\n\n" + "\n".join(extracted_blocks[:3])

FEATURES_JSON = INSTALL_DIR / "features.json"

def check_features_pipeline():
    """Checks features.json for pending feature requests and implements them autonomously."""
    if not FEATURES_JSON.exists():
        return False

    try:
        with open(FEATURES_JSON, "r") as f:
            data = json.load(f)
    except Exception as e:
        log_error(f"Failed to read features.json: {e}")
        return False

    features = data.get("features", [])
    pending_feature = None
    pending_idx = -1
    for idx, feat in enumerate(features):
        if feat.get("status") == "pending" or feat.get("status") == "failed":
            # Allow retrying failed features
            pending_feature = feat
            pending_idx = idx
            break

    if not pending_feature:
        return False

    feat_id = pending_feature.get("id")
    title = pending_feature.get("title")
    desc = pending_feature.get("description")

    log_info(f"Found pending feature request: {title} ({feat_id})")

    # Mark as in_progress
    features[pending_idx]["status"] = "in_progress"
    try:
        with open(FEATURES_JSON, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log_error(f"Failed to update features.json: {e}")
        return False

    send_telegram_text(
        f"🤖 *Jan: Yeni Özellik Geliştirme Başladı*\n\n"
        f"📍 *Özellik*: `{title}`\n"
        f"📝 *Açıklama*: {desc}\n"
        f"⚡ Jan kod tabanını analiz ediyor..."
    )

    # Load current sre_daemon.py source code to provide general context
    sre_daemon_path = INSTALL_DIR / "sre_daemon.py"
    if not sre_daemon_path.exists():
        log_error("sre_daemon.py not found, aborting.")
        return False

    with open(sre_daemon_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    classes_in_file = []
    for line in source_code.splitlines():
        if line.startswith("class "):
            parts = line.split("class ")[1].split("(")[0].split(":")[0].strip()
            classes_in_file.append(parts)
    classes_list_str = "\n".join(f"- {c}" for c in classes_in_file)

    code_context = extract_feature_context(source_code, title, desc)

    # Formulate prompt for new features
    prompt = f"""You are Jan, the self-evolving AI SRE developer agent.
Your primary task is to implement a new feature in 'sre_daemon.py' and its corresponding tests based on the feature request and the code context below.

[CLASSES DEFINED IN SRE_DAEMON.PY]
{classes_list_str}

[FEATURE REQUEST]
ID: {feat_id}
Title: {title}
Description: {desc}

[CODE CONTEXT SKELETON AND MATCHING METHODS IN SRE_DAEMON.PY]
{code_context}

You must write clean, correct, and robust code for the feature.
IMPORTANT RULES:
1. Do NOT nest your new methods/functions inside existing functions.
2. If you are adding a class method (e.g. to SREDaemon or MetricsCollector), find a method definition of that class in the [CODE CONTEXT], and replace that method with the original method + your new method at the same indentation level.
3. Do NOT import non-standard external math libraries (like numpy, pandas, sklearn, or scipy). Write the trend-line slope calculation in pure Python using simple arithmetic (e.g., least squares slope = covariance(x,y)/variance(x)). This ensures zero external dependencies and zero ImportError.
4. Ensure all helper methods, imports, and variables you use are fully defined or imported.
5. Do NOT guess or invent class names (like SREDaemon). Use the actual class names shown in the [CODE CONTEXT SKELETON] and [CODE CONTEXT] (e.g., MetricsCollector for metric collection and analysis).
6. When writing unit tests for MetricsCollector, remember that its __init__ constructor requires an orchestrator argument. You can instantiate it in tests using a mock object, e.g. collector = MetricsCollector(MagicMock()).



Output your response strictly as an XML document using CDATA blocks for search and replace code segments, like this:
<patches>
  <patch>
    <target>sre_daemon.py</target>
    <search><![CDATA[
    def _handle_predictive_fix(self, entity: str, metric_type: str, current_val: float, ema_val: float):
        # ... original code ...
        # ... original code ...
]]></search>
    <replace><![CDATA[
    def _handle_predictive_fix(self, entity: str, metric_type: str, current_val: float, ema_val: float):
        # ... original code ...
        # ... original code ...

    def predict_scaling_advisor(self):
        # your new method at class-level indentation (same indentation as _handle_predictive_fix)
        pass
]]></replace>
  </patch>
  <patch>
    <target>tests/test_predictive.py</target>
    <search><![CDATA[]]></search>
    <replace><![CDATA[
import unittest
# new test contents here
    ]]></replace>
  </patch>
</patches>
"""

    response_text, provider = query_llm_cascade(prompt)
    if not response_text:
        log_error("Could not obtain a response from any LLM in the cascade.")
        features[pending_idx]["status"] = "failed"
        with open(FEATURES_JSON, "w") as f:
            json.dump(data, f, indent=2)
        return False

    log_info(f"LLM cascade response received from {provider}.")
    try:
        with open(INSTALL_DIR / "jan_last_patch_response.log", "w") as rf:
            rf.write(response_text)
    except Exception:
        pass


    patches = parse_xml_patches(response_text)
    if not patches:
        log_error("No valid XML patches could be parsed.")
        with open(INSTALL_DIR / "jan_raw_response.txt", "w") as rf:
            rf.write(response_text)
        features[pending_idx]["status"] = "failed"
        with open(FEATURES_JSON, "w") as f:
            json.dump(data, f, indent=2)
        return False

    try:
        # Apply patches atomically
        applied_count = 0
        for p in patches:
            target_rel = p.get("target")
            if not target_rel:
                continue
            target_path = INSTALL_DIR / target_rel
            search_str = p.get("search", "")
            replace_str = p.get("replace", "")

            # If file doesn't exist, create it (new file support)
            if not target_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(replace_str, encoding="utf-8")
                applied_count += 1
                log_info(f"Created new file: {target_rel}")
                continue

            current_content = target_path.read_text(encoding="utf-8")
            search_str, replace_str, count = self_heal_patch_indentation(search_str, replace_str, current_content, target_rel)
            if count == 0:
                log_error(f"Search block not found in file {target_rel}:\n{search_str[:150]}")
                continue
            if count > 1:
                log_error(f"Search block matches multiple locations ({count}) in {target_rel}, patch is ambiguous.")
                continue

            new_content = current_content.replace(search_str, replace_str, 1)
            target_path.write_text(new_content, encoding="utf-8")
            applied_count += 1
            log_info(f"Applied patch to {target_rel}.")

        if applied_count == 0:
            log_error("No patches could be applied successfully.")
            features[pending_idx]["status"] = "failed"
            with open(FEATURES_JSON, "w") as f:
                json.dump(data, f, indent=2)
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
                subprocess.run(["git", "-C", str(INSTALL_DIR), "add", "."], check=True)
                subprocess.run(["git", "-C", str(INSTALL_DIR), "commit", "-m", f"jan: otonom ozellik gelistirme - {title}"], check=True)
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

            # Update features.json status to implemented
            features[pending_idx]["status"] = "implemented"
            with open(FEATURES_JSON, "w") as f:
                json.dump(data, f, indent=2)

            # Notify via Telegram
            send_telegram_text(
                f"🤖 *Jan: Yeni Özellik Başarıyla Devreye Alındı!* ✅\n\n"
                f"📍 *Özellik*: `{title}`\n"
                f"⏱ *Doğrulama*: `pytest` başarıyla tamamlandı\n"
                f"📦 *Push*: GitHub'a gönderildi\n"
                f"🔄 *Servis*: Yeniden başlatıldı."
            )
            return True
        else:
            log_error(f"Verification gate FAILED! Pytest output:\n{test_res.stdout}\n{test_res.stderr}")
            # Rollback
            subprocess.run(["git", "-C", str(INSTALL_DIR), "checkout", "--", "."], check=True)
            log_info("Git checkout executed. Rollback successful.")
            
            # Remove any untracked files
            subprocess.run(["git", "-C", str(INSTALL_DIR), "clean", "-fd"], check=True)

            features[pending_idx]["status"] = "failed"
            with open(FEATURES_JSON, "w") as f:
                json.dump(data, f, indent=2)

            send_telegram_text(
                f"❌ *Jan: Özellik Geliştirme Başarısız*\n\n"
                f"Özellik: `{title}`\n"
                "Üretilen kod veya testler mevcut sistemi bozdu, değişiklikler geri alındı.\n"
                f"Hata: `{md_escape(test_res.stderr[:200] or test_res.stdout[:200])}`"
            )
            return False

    except Exception as ex:
        log_error(f"An error occurred during feature implementation: {ex}")
        try:
            features[pending_idx]["status"] = "failed"
            with open(FEATURES_JSON, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
        # Rollback as last resort
        try:
            subprocess.run(["git", "-C", str(INSTALL_DIR), "checkout", "--", "."], check=True)
            subprocess.run(["git", "-C", str(INSTALL_DIR), "clean", "-fd"], check=True)
        except Exception:
            pass
        return False

def md_escape(text: str) -> str:
    for char in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(char, f"\\{char}")
    return text

def main():
    log_info("Jan Developer Agent started checking logs, db, and features...")
    
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
        tb = tracebacks[-1]
        log_info(f"Traceback detected in {tb['source']}.")
        analyze_and_patch(tb["content"])
        return

    # Check 3: Feature request pipeline
    if check_features_pipeline():
        return

    log_info("No fresh tracebacks, failed actions, or pending features detected. Going to sleep.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
SRE Daemon v5 — Hardened HITL & Self-Healing Engine (Pi 5)
=============================================================
A production-grade system-wide reliability daemon for Raspberry Pi 5.
Implements:
1. SQLite-based HITL (Human-in-the-loop) action state machine.
2. Detached crash-loop PID and Heartbeat monitoring watchdog.
3. Whitelisted Telegram callback polling listener.
4. Atomic file modification with py_compile and import smoke testing.
5. Deterministic risk classification.
"""

import os
import sys
import json
import time
import logging
import threading
import signal
import re
import subprocess
import sqlite3
import hashlib
import difflib
import stat
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
import requests

# ── Configuration & Paths ────────────────────────────────────
MAC_IP            = os.getenv("MAC_IP", "")
MAC_OLLAMA_URL    = f"http://{MAC_IP}:11434"
PI_OLLAMA_URL     = os.getenv("PI_OLLAMA_URL", "http://localhost:11434")
HEAL_LOG          = os.getenv("HEAL_LOG", "/home/pi/sre/heal_log.jsonl")
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
XAI_API_KEY       = os.getenv("XAI_API_KEY", "")

DB_PATH           = Path("/home/pi/sre/sre_state.db")
HEARTBEAT_PATH    = Path("/home/pi/sre/.heartbeat")
SELF_PATH         = Path(__file__).resolve()
MAX_LOCAL_TRIES   = 3
OLLAMA_TIMEOUT    = 60
ANTHROPIC_TIMEOUT = 60
RATE_LIMIT_SECONDS = 600
DOCKER_BURST_LIMIT = 60

# Locks & Cooldown Cache
ACTION_LOCK       = threading.Lock()
SELF_FIX_LOCK     = threading.Lock()
DECLINED_ERRORS   = {}  # error_hash -> timestamp
DECLINED_COOLDOWN = 3600  # 1 hour cooldown for rejected issues

NOISE_PATTERNS = re.compile(
    r"(DEBUG|audit\(|systemd-logind|NetworkManager.*state|"
    r"DHCP|avahi|dbus-daemon|Bluetooth|btusb|rfkill|"
    r"CRON|anacron|logrotate|sre-daemon|sre-bridge)",
    re.IGNORECASE
)

PROJECT_MAP = {
    "trihonor":       "[TriHonor-API]",
    "coachonurai":    "[AI-Coach]",
    "bikefit-api":    "[BikeFit-API]",
    "bikefit":        "[BikeFit-API]",
    "nginx":          "[Nginx]",
    "postgres":       "[PostgreSQL]",
    "immich":         "[Immich]",
    "vaultwarden":    "[Vaultwarden]",
    "hailo":          "[Hailo-AI-HW]",
    "hailort":        "[Hailo-AI-HW]",
    "docker":         "[Docker-Engine]",
    "kernel":         "[Kernel-HW]",
    "usb":            "[Kernel-HW]",
    "camera":         "[Kernel-HW]",
    "v4l":            "[Kernel-HW]",
}

log_dir = Path("/home/pi/sre")
log_path = log_dir / "daemon.log" if log_dir.exists() else Path("daemon.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(str(log_path)),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("sre-daemon")

# ── Helper Functions ─────────────────────────────────────────
def md_escape(text: str) -> str:
    if text is None:
        return ""
    for ch in ["\\", "_", "*", "`"]:
        text = text.replace(ch, f"\\{ch}")
    return text

def atomic_write_text(target: Path, content: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.stem}.__sre_tmp__{target.suffix}")
    orig_stat = target.stat() if target.exists() else None

    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    if orig_stat:
        os.chmod(tmp_path, stat.S_IMODE(orig_stat.st_mode))
        try:
            os.chown(tmp_path, orig_stat.st_uid, orig_stat.st_gid)
        except PermissionError:
            pass

    os.replace(tmp_path, target)

def cleanup_old_prefix_tags(repo_path="/home/pi/sre", prefix="pre-fix-", max_age_hours=24):
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, "tag", "--list", f"{prefix}*"],
            capture_output=True, text=True, timeout=10, check=True
        )
        now = time.time()
        for tag in res.stdout.splitlines():
            tag = tag.strip()
            if not tag:
                continue
            m = re.match(rf"^{re.escape(prefix)}(\d+)", tag)
            if not m:
                continue
            ts = int(m.group(1))
            if now - ts > max_age_hours * 3600:
                subprocess.run(
                    ["git", "-C", repo_path, "tag", "-d", tag],
                    capture_output=True, timeout=10
                )
    except Exception as e:
        logger.warning("Tag cleanup hatası: %s", str(e))

# ── SQLite HITL State Manager ────────────────────────────────
def init_db():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_hash TEXT,
                    risk_level TEXT,
                    actions_json TEXT,
                    status TEXT,
                    created_at TEXT,
                    expires_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS heal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_hash TEXT NOT NULL,
                    error_message TEXT,
                    project_tag TEXT,
                    risk_level TEXT,
                    llm_prompt_used TEXT,
                    llm_response_raw TEXT,
                    llm_source TEXT,
                    actions_json TEXT,
                    execution_output TEXT,
                    success INTEGER,
                    duration_seconds REAL,
                    created_at TEXT
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('autonomous_mode', '0')"
            )
            conn.commit()
    except Exception as e:
        logger.error("SQLite init hatası: %s", str(e))

def get_daemon_setting(key: str, default: str = "") -> str:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else default
    except Exception:
        return default

def set_daemon_setting(key: str, value: str):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
            conn.commit()
    except Exception as e:
        logger.error("Setting yazma hatası: %s", str(e))

def save_heal_history(
    error_hash: str, error_message: str, project_tag: str, risk_level: str,
    prompt: str, llm_response: str, llm_source: str,
    actions: list, execution_output: list, success: bool, duration: float
):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO heal_history
                (error_hash, error_message, project_tag, risk_level, llm_prompt_used,
                 llm_response_raw, llm_source, actions_json, execution_output, success,
                 duration_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                error_hash, error_message[:2000], project_tag, risk_level,
                prompt[:4000], (llm_response or "")[:4000], llm_source,
                json.dumps(actions), json.dumps(execution_output),
                1 if success else 0, duration,
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
    except Exception as e:
        logger.error("heal_history kayıt hatası: %s", str(e))

def get_heal_history_for_hash(error_hash: str, limit: int = 10) -> list:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT actions_json, execution_output, success, created_at
                FROM heal_history
                WHERE error_hash = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (error_hash, limit))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []

def add_pending_action(error_hash: str, risk_level: str, actions: List[Dict[str, Any]]) -> int:
    init_db()
    created = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    actions_str = json.dumps(actions)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO pending_actions (error_hash, risk_level, actions_json, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (error_hash, risk_level, actions_str, "pending", created, expires)
        )
        conn.commit()
        return cursor.lastrowid

def try_process_action(action_id: int, target_status: str) -> Optional[List[Dict[str, Any]]]:
    """Idempotent and thread-safe transition of pending actions."""
    with ACTION_LOCK:
        init_db()
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,))
            row = cursor.fetchone()
            if not row:
                return None
            
            if row["status"] != "pending":
                logger.warning("Action %d zaten işlenmiş (durum: %s)", action_id, row["status"])
                return None
            
            # Check expiration
            expires = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) > expires:
                cursor.execute("UPDATE pending_actions SET status = 'timed_out' WHERE id = ?", (action_id,))
                conn.commit()
                logger.warning("Action %d zaman aşımına uğramış.", action_id)
                return None
            
            cursor.execute("UPDATE pending_actions SET status = ? WHERE id = ?", (target_status, action_id))
            conn.commit()
            
            if target_status == "approved":
                return json.loads(row["actions_json"])
            return None

def timeout_worker():
    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            with ACTION_LOCK:
                init_db()
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("""
                        UPDATE pending_actions
                        SET status = 'timed_out'
                        WHERE status = 'pending' AND expires_at < ?
                    """, (now_iso,))
                    conn.commit()
        except Exception as e:
            logger.warning("Timeout worker hatası: %s", str(e))
        time.sleep(15)

# ── Heartbeat Thread ─────────────────────────────────────────
def start_heartbeat():
    def _run():
        while True:
            try:
                HEARTBEAT_PATH.write_text(str(time.time()))
            except Exception:
                pass
            time.sleep(5)
    t = threading.Thread(target=_run, name="heartbeat", daemon=True)
    t.start()

# ── Rate Limiter ─────────────────────────────────────────────
class RateLimiter:
    def __init__(self):
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_process(self, key: str, limit_seconds: int = RATE_LIMIT_SECONDS) -> bool:
        with self._lock:
            now = time.time()
            last = self._seen.get(key, 0)
            if now - last < limit_seconds:
                return False
            self._seen[key] = now
            if len(self._seen) > 500:
                oldest = min(self._seen, key=self._seen.get)
                del self._seen[oldest]
            return True

# ── Watchers ─────────────────────────────────────────────────
class JournalWatcher:
    JOURNAL_CMD = [
        "journalctl", "-f", "-n", "0", "-p", "err", "-o", "json",
        "--no-pager", "--no-hostname"
    ]

    def __init__(self, rate_limiter: RateLimiter, callback):
        self.rate_limiter = rate_limiter
        self.callback = callback
        self._stop_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        threading.Thread(target=self._watch, name="journal-watcher", daemon=True).start()
        logger.info("JournalWatcher aktif.")

    def stop(self):
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def _watch(self):
        while not self._stop_event.is_set():
            try:
                self._proc = subprocess.Popen(
                    self.JOURNAL_CMD,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                for raw_line in self._proc.stdout:
                    if self._stop_event.is_set():
                        break
                    self._process_line(raw_line.strip())
            except Exception as e:
                logger.error("JournalWatcher hata: %s", str(e))
                time.sleep(10)

    def _process_line(self, raw_line: str):
        if not raw_line:
            return
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            return

        message   = entry.get("MESSAGE", "")
        unit      = entry.get("_SYSTEMD_UNIT", "-")
        ident     = entry.get("SYSLOG_IDENTIFIER", "-")
        priority  = int(entry.get("PRIORITY", 6))

        if priority > 3:
            return
        if NOISE_PATTERNS.search(f"{unit} {ident} {message}"):
            return

        safe_msg = str(message)[:2000]
        if not safe_msg:
            return

        # Derive project tag
        combined = f"{unit} {ident} {safe_msg}".lower()
        tag = "[System]"
        for keyword, mapped_tag in PROJECT_MAP.items():
            if keyword in combined:
                tag = mapped_tag
                break

        rate_key = f"journal:{unit}:{safe_msg[:80]}"
        if not self.rate_limiter.should_process(rate_key):
            return

        priority_label = {0: "EMERG", 1: "ALERT", 2: "CRIT", 3: "ERR"}.get(priority, "ERR")
        tagged = f"{tag} [{priority_label}][{ident}] {safe_msg}"
        self.callback(tagged, tag)

class DockerWatcher:
    CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{64}$")
    DOCKER_EVENTS_CMD = [
        "docker", "events", "--filter", "type=container",
        "--filter", "event=die", "--filter", "event=oom", "--filter", "event=kill",
        "--format", "{{json .}}"
    ]

    def __init__(self, rate_limiter: RateLimiter, callback):
        self.rate_limiter = rate_limiter
        self.callback = callback
        self._stop_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        threading.Thread(target=self._watch, name="docker-watcher", daemon=True).start()
        logger.info("DockerWatcher aktif.")

    def stop(self):
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def _watch(self):
        while not self._stop_event.is_set():
            try:
                self._proc = subprocess.Popen(
                    self.DOCKER_EVENTS_CMD,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                for raw_line in self._proc.stdout:
                    if self._stop_event.is_set():
                        break
                    self._process_event(raw_line.strip())
            except Exception as e:
                logger.error("DockerWatcher hata: %s", str(e))
                time.sleep(15)

    def _process_event(self, raw_line: str):
        if not raw_line:
            return
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return

        container_id   = event.get("id", "")
        container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "unknown")
        action         = event.get("Action", "die")

        if not self.CONTAINER_ID_RE.match(container_id):
            return

        tag = "[Docker]"
        name_lower = container_name.lower()
        for keyword, mapped_tag in PROJECT_MAP.items():
            if keyword in name_lower:
                tag = mapped_tag
                break

        rate_key = f"docker:{container_name}:{action}"
        if not self.rate_limiter.should_process(rate_key, DOCKER_BURST_LIMIT):
            return

        # Fetch logs
        try:
            res = subprocess.run(["docker", "logs", "--tail", "30", container_id], capture_output=True, text=True, timeout=10)
            logs = (res.stdout + res.stderr).strip()
            error_lines = [l for l in logs.splitlines() if re.search(r"error|exception|traceback|fatal|fail", l, re.IGNORECASE)]
            log_summary = "\n".join(error_lines[-20:]) if error_lines else logs[-1000:]
        except Exception as e:
            log_summary = f"Log hatası: {str(e)}"

        tagged = f"{tag} [DOCKER-{action.upper()}] Container '{container_name}' çöktü.\nSon loglar:\n{log_summary}"
        self.callback(tagged, tag)

# ── Clients ──────────────────────────────────────────────────
class MacChecker:
    def is_mac_online(self) -> bool:
        if not MAC_IP:
            return False
        try:
            resp = requests.get(f"{MAC_OLLAMA_URL}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

class OllamaClient:
    def query(self, base_url: str, model: str, prompt: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
                timeout=OLLAMA_TIMEOUT,
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as e:
            logger.warning("Ollama hatası (%s): %s", base_url, str(e))
        return None

class GeminiClient:
    def query(self, prompt: str) -> Optional[str]:
        if not GEMINI_API_KEY:
            return None
        try:
            url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ]
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            res_json = resp.json()
            text = res_json["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()
        except Exception as e:
            logger.error("Gemini API hatası: %s", str(e))
        return None

class GroqClient:
    def query(self, prompt: str) -> Optional[str]:
        if not GROQ_API_KEY:
            return None
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            res_json = resp.json()
            text = res_json["choices"][0]["message"]["content"]
            return text.strip()
        except Exception as e:
            logger.error("Groq API hatası: %s", str(e))
        return None

class XAIClient:
    def query(self, prompt: str) -> Optional[str]:
        if not XAI_API_KEY:
            return None
        try:
            url = "https://api.x.ai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "grok-2-1212",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            res_json = resp.json()
            text = res_json["choices"][0]["message"]["content"]
            return text.strip()
        except Exception as e:
            logger.error("xAI/Grok API hatası: %s", str(e))
        return None

class AnthropicClient:
    def query(self, prompt: str) -> Optional[str]:
        if not ANTHROPIC_KEY:
            return None
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]},
                timeout=ANTHROPIC_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except Exception as e:
            logger.error("Anthropic API hatası: %s", str(e))
        return None

# ── Healing Orchestrator & Action Runner ─────────────────────
class HealingOrchestrator:
    def __init__(self):
        self.mac_checker  = MacChecker()
        self.ollama       = OllamaClient()
        self.gemini       = GeminiClient()
        self.groq         = GroqClient()
        self.xai          = XAIClient()
        self.anthropic    = AnthropicClient()

    def handle_error(self, tagged_line: str, project_tag: str, err_hash: str = None):
        if not err_hash:
            import hashlib
            err_hash = hashlib.md5(tagged_line.encode("utf-8", errors="ignore")).hexdigest()
        
        # Cooldown / Dedup Check
        now = time.time()
        if err_hash in DECLINED_ERRORS:
            if now - DECLINED_ERRORS[err_hash] < DECLINED_COOLDOWN:
                logger.info("Aynı hata son 1 saatte reddedilmiş, yoksayılıyor: %s", project_tag)
                return

        threading.Thread(
            target=self._heal, args=(tagged_line, project_tag, err_hash),
            name=f"healer-{project_tag}", daemon=True
        ).start()

    def _build_prompt(self, tagged_line: str, project_tag: str, err_hash: str = None) -> str:
        project_name = project_tag.strip("[]")

        # Reflexive Learning: inject past outcomes for this error hash
        past_context = ""
        if err_hash:
            past = get_heal_history_for_hash(err_hash, limit=10)
            if past:
                past_context = "\n\nGEÇMİŞ ONARIM GİRİŞİMLERİ (Bu hata için önceki sonuçlar):\n"
                for p in past:
                    status_str = "BAŞARILI ✅" if p["success"] else "BAŞARISIZ ❌"
                    try:
                        acts = json.loads(p["actions_json"])
                        act_summary = "; ".join(
                            f"{a.get('type')} -> {a.get('target','?')} ({str(a.get('payload',''))[:60]})"
                            for a in acts
                        )
                    except Exception:
                        act_summary = p["actions_json"][:200]
                    past_context += f"- [{p['created_at'][:16].replace('T', ' ')}] {status_str}: {act_summary}\n"
                past_context += "\nYukarıdaki geçmiş sonuçları dikkate al. Başarısız olan aksiyonları tekrar deneme. Başarılı olanları tercih et.\n"

        return (
            f"Sen bir kıdemli Linux SRE ve backend geliştiricisisin.\n"
            f"Sistem: Raspberry Pi 5 | Proje/Servis: {project_name}\n\n"
            "Pi üzerindeki bilinen proje dizinleri ve docker-compose yolları şunlardır:\n"
            "- BikeFit-API (bikefit-api): Proje dizini: /home/pi/bikefit-api | docker-compose dizini: /home/pi/bikefit (docker-compose.yml burada yer alır)\n"
            "- AI-Coach (compose servis adı: api, container adı: coachonurai-api): Proje dizini: /home/pi/projects/AI-Coach | docker-compose dizini: /home/pi/projects/AI-Coach (docker-compose.yml burada yer alır). Doğru komut: docker compose up -d --build api\n"
            "- TriHonor-API (trihonor-api-prod): Proje dizini: /home/pi/TriHonor-API | docker-compose dizini: /home/pi/TriHonor-API (docker-compose.prod.yml burada yer alır)\n"
            f"{past_context}\n"
            "Aşağıdaki sistem hata mesajını analiz et:\n"
            "1. Kök neden açıklaması\n"
            "2. Önlem açıklaması\n"
            "3. Eylemler ('actions') dizisi.\n\n"
            "Eylemler türleri:\n"
            "- 'append': target='/home/pi/...', payload='...'\n"
            "- 'write': target='/home/pi/...', payload='...'\n"
            "- 'shell': target='/home/pi/...', payload='docker compose up -d ...'\n\n"
            f"HATA:\n{tagged_line[:1500]}\n\n"
            "JSON formatında yanıt ver. Kod blokları kullanma.\n"
            "{\n"
            '  "root_cause": "...",\n'
            '  "prevention": "...",\n'
            '  "actions": [{"type": "...", "target": "...", "payload": "..."}]\n'
            "}"
        )

    def _classify_risk(self, actions: List[Dict[str, Any]]) -> str:
        max_risk = "Low"
        for act in actions:
            act_type = act.get("type", "")
            target = act.get("target", "")
            
            if target and Path(target).resolve() == SELF_PATH:
                return "Critical"
            
            if act_type in ("write", "replace"):
                if target.endswith((".py", ".js", ".jsx", ".db", ".json", ".sh")):
                    max_risk = "High" if max_risk != "Critical" else "Critical"
            elif act_type == "append" and "requirements.txt" in target:
                if max_risk == "Low":
                    max_risk = "Medium"
        return max_risk

    def execute_approved_actions(self, actions: List[Dict[str, Any]], action_id: int) -> List[Dict[str, Any]]:
        # ... (Geri kalan aynı kalacak) ...
        return []

    def _heal(self, tagged_line: str, project_tag: str, err_hash: str):
        # ... (Geri kalan aynı kalacak) ...
        pass

    def _write_heal_log(self, error, fix, source, success, tag, actions=None):
        # ... (Geri kalan aynı kalacak) ...
        pass

def main():
    main()

if __name__ == "__main__":
    main()

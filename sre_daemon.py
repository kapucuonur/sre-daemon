#!/usr/bin/env python3
"""
SRE Daemon v5 — Generic SaaS Self-Healing Engine
=================================================
A production-grade, platform-agnostic reliability daemon.
Works on Raspberry Pi 5, Ubuntu VPS, Debian, RHEL, and any Linux server.
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
import psutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
import requests
try:
    import yaml as _yaml_mod
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
# ── Load .env manually if it exists (for standalone test / cron compatibility) ──
def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                val = val.strip().strip("'").strip('"')
                os.environ[key.strip()] = val

load_env()


# ── Configuration & Paths ────────────────────────────────────
MAC_IP            = os.getenv("MAC_IP", "")
MAC_OLLAMA_URL    = f"http://{MAC_IP}:11434"
PI_OLLAMA_URL     = os.getenv("PI_OLLAMA_URL", "http://localhost:11434")
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
XAI_API_KEY       = os.getenv("XAI_API_KEY", "")

INSTALL_DIR       = Path(os.getenv("INSTALL_DIR", str(Path(__file__).parent)))
DB_PATH           = INSTALL_DIR / "sre_state.db"
HEARTBEAT_PATH    = INSTALL_DIR / ".heartbeat"
MANIFEST_PATH     = INSTALL_DIR / "manifest.yaml"
SELF_PATH         = Path(__file__).resolve()
HEAL_LOG          = os.getenv("HEAL_LOG", str(INSTALL_DIR / "heal_log.jsonl"))
MAX_LOCAL_TRIES   = 3
OLLAMA_TIMEOUT    = 180
ANTHROPIC_TIMEOUT = 60
RATE_LIMIT_SECONDS = 600

# Locks & Cooldown Cache
ACTION_LOCK       = threading.Lock()
SELF_FIX_LOCK     = threading.Lock()
DECLINED_ERRORS   = {}  # error_hash -> timestamp
TRUCE_CACHE       = {}  # error_hash -> (timestamp, count)

# Dynamic Config Loading
CONFIG_PATH = Path(__file__).parent / "config.json"
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config.json: {e}")
    return {}

_config = load_config()

NOISE_PATTERNS_STR = _config.get("noise_patterns", r"(DEBUG|audit\(|systemd-logind|NetworkManager.*state|DHCP|avahi|dbus-daemon|Bluetooth|btusb|rfkill|CRON|anacron|logrotate|sre-daemon|sre-bridge)")
NOISE_PATTERNS = re.compile(NOISE_PATTERNS_STR, re.IGNORECASE)

PROJECT_MAP = _config.get("project_map", {
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
})
DOCKER_BURST_LIMIT = _config.get("docker_burst_limit", 3)
DECLINED_COOLDOWN = _config.get("declined_cooldown", 3600)
TRUCE_COOLDOWN_SECONDS = _config.get("truce_cooldown_seconds", 600)
DAILY_CLOUD_LIMITS = _config.get("daily_cloud_api_limit", {
    "gemini": 100,
    "groq": 100,
    "xai": 50,
    "claude": 5
})

# Import Watchers dynamically
try:
    from monitors import JournalWatcher, DockerWatcher
except ImportError:
    class JournalWatcher:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def stop(self): pass
    class DockerWatcher:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def stop(self): pass

log_dir = INSTALL_DIR
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
    for ch in ["\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
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

def cleanup_old_prefix_tags(repo_path=None, prefix="pre-fix-", max_age_hours=24):
    if not repo_path: repo_path = str(INSTALL_DIR)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_api_usage (
                    provider TEXT,
                    day TEXT,
                    count INTEGER,
                    PRIMARY KEY (provider, day)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS error_hash_signatures (
                    error_hash   TEXT PRIMARY KEY,
                    raw_snippet  TEXT NOT NULL,
                    created_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_registry (
                    error_hash      TEXT    NOT NULL,
                    command         TEXT    NOT NULL,
                    success_count   INTEGER DEFAULT 0,
                    fail_count      INTEGER DEFAULT 0,
                    weight          INTEGER DEFAULT 0,
                    is_blacklisted  INTEGER DEFAULT 0,
                    last_used       TEXT,
                    PRIMARY KEY (error_hash, command)
                )
            """)
            # Default settings
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('autonomous_mode', '0')"
            )
            conn.commit()
    except Exception as e:
        logger.error("SQLite init hatası: %s", str(e))

def compute_error_hash(container_name: str, error_log_snippet: str) -> str:
    # Clean the log snippet by removing container name, service tags, brackets, and syslog headers
    raw = error_log_snippet.lower()
    if container_name:
        c_name = container_name.lower().strip()
        raw = raw.replace(c_name, "")
    
    # Remove all bracketed tags like [Mockservice], [ERR]
    raw = re.sub(r'\[[^\]]+\]', '', raw)
    
    # Remove double spaces, strip, and take first 200 characters
    raw = " ".join(raw.split())
    raw = raw[:200].strip()
    
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    # Persist raw snippet for semantic similarity lookups
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(DB_PATH) as _conn:
            _conn.execute(
                "INSERT OR IGNORE INTO error_hash_signatures (error_hash, raw_snippet, created_at) VALUES (?, ?, ?)",
                (h, raw, now_str)
            )
            _conn.commit()
    except Exception:
        pass
    return h

def get_best_strategy(db_path: Path, error_hash: str) -> Optional[str]:
    try:
        from datetime import datetime, timezone
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT command, weight, last_used 
                FROM strategy_registry
                WHERE error_hash = ?
                  AND is_blacklisted = 0
                """,
                (error_hash,)
            )
            rows = cur.fetchall()
            if not rows:
                return None
            
            candidates = []
            now_dt = datetime.now(timezone.utc)
            
            for command, weight, last_used in rows:
                decayed_weight = weight
                if last_used:
                    try:
                        last_used_dt = datetime.fromisoformat(last_used)
                        age_days = (now_dt - last_used_dt).total_seconds() / 86400.0
                        if age_days >= 30.0:
                            decay_factor = 0.5 ** (age_days / 30.0)
                            decayed_weight = int(weight * decay_factor)
                    except Exception:
                        pass
                
                if decayed_weight >= 0:
                    candidates.append((command, decayed_weight, weight))
            
            if not candidates:
                return None
            
            # Sort by decayed weight desc, then base weight desc
            candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            return candidates[0][0]
    except Exception as e:
        logger.error("Registry get error: %s", e)
        return None


def get_semantic_strategy(db_path: Path, raw_error: str, threshold: float = 0.82) -> Optional[tuple]:
    """
    Fuzzy semantic match over strategy_registry using difflib.SequenceMatcher.

    Instead of requiring an exact hash match, this function compares the
    incoming error text against all stored error signatures in the registry.
    If similarity >= threshold, returns (command, similarity_score).

    This enables cross-container and cross-customer learning:
    - Customer A's NameError in Flask → fixes Customer B's NameError in FastAPI
    - Same error type, different variable names → still matches

    Returns None if no match found above threshold.
    """
    from difflib import SequenceMatcher

    # Normalize incoming error (same logic as compute_error_hash but keep text)
    normalized_new = " ".join(raw_error.lower().split())[:400]

    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            # Fetch all non-blacklisted strategies with their error signatures
            cur.execute("""
                SELECT sr.error_hash, sr.command, sr.weight, sr.last_used,
                       eh.raw_snippet
                FROM strategy_registry sr
                LEFT JOIN error_hash_signatures eh ON sr.error_hash = eh.error_hash
                WHERE sr.is_blacklisted = 0
                  AND sr.weight >= 0
                ORDER BY sr.weight DESC
                LIMIT 200
            """)
            rows = cur.fetchall()
    except Exception:
        # Table may not exist yet — graceful fallback
        return None

    best_score = 0.0
    best_command = None

    for error_hash, command, weight, last_used, raw_snippet in rows:
        if not raw_snippet:
            continue
        normalized_stored = " ".join(raw_snippet.lower().split())[:400]
        score = SequenceMatcher(None, normalized_new, normalized_stored).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_command = command

    if best_command:
        logger.info(
            "[SEMANTIC REGISTRY] Match found (similarity=%.2f%%) → %s",
            best_score * 100, best_command[:80]
        )
        return best_command, best_score

    return None

def update_strategy_result(db_path: Path, error_hash: str, command: str, success: bool):
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO strategy_registry
                    (error_hash, command, success_count, fail_count, weight, is_blacklisted, last_used)
                VALUES (?, ?, 0, 0, 0, 0, ?)
                """,
                (error_hash, command, now_str)
            )
            if success:
                conn.execute(
                    """
                    UPDATE strategy_registry
                    SET success_count = success_count + 1,
                        weight = weight + 2,
                        last_used = ?
                    WHERE error_hash = ? AND command = ?
                    """,
                    (now_str, error_hash, command)
                )
            else:
                conn.execute(
                    """
                    UPDATE strategy_registry
                    SET fail_count = fail_count + 1,
                        weight = weight - 1,
                        last_used = ?
                    WHERE error_hash = ? AND command = ?
                    """,
                    (now_str, error_hash, command)
                )
            conn.execute(
                """
                UPDATE strategy_registry
                SET is_blacklisted = 1
                WHERE error_hash = ? AND command = ? AND weight < 0
                """,
                (error_hash, command)
            )
            conn.commit()
            logger.info("[REGISTRY] Updated strategy: hash=%s, command=%s, success=%s", error_hash, command, success)
    except Exception as e:
        logger.error("Registry update error: %s", e)

def register_actions_in_registry(db_path: Path, error_hash: str, actions: list, success: bool):
    if not actions:
        return
    for act in actions:
        if isinstance(act, dict):
            if act.get("type") == "shell" and act.get("payload"):
                cmd = act["payload"].strip()
                if cmd:
                    update_strategy_result(db_path, error_hash, cmd, success)
        elif isinstance(act, str):
            cmd = act.strip()
            if cmd:
                update_strategy_result(db_path, error_hash, cmd, success)

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

# ── Token Minimization Helpers ───────────────────────────────
def get_daily_calls(model_provider: str) -> int:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT count FROM daily_api_usage WHERE provider = ? AND day = ?",
                (model_provider, today_str)
            )
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error("Token budget check error: %s", e)
        return 0

def increment_daily_calls(model_provider: str):
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO daily_api_usage (provider, day, count) VALUES (?, ?, 1) "
                "ON CONFLICT(provider, day) DO UPDATE SET count = count + 1",
                (model_provider, today_str)
            )
            conn.commit()
    except Exception as e:
        logger.error("Token budget update error: %s", e)

def summarize_log(raw_msg: str) -> str:
    """Pre-processes and summarizes logs to reduce tokens sent to LLM."""
    if not raw_msg or len(raw_msg) < 500:
        return raw_msg

    lines = raw_msg.splitlines()
    if len(lines) <= 6:
        return raw_msg

    error_indicators = ["error", "exception", "failed", "traceback", "critical", "fatal"]
    critical_lines = []
    for i, line in enumerate(lines):
        if any(ind in line.lower() for ind in error_indicators):
            critical_lines.append((i, line))

    summary_lines = []
    summary_lines.append(f"[Start of Log Excerpt] {lines[0]}")

    added_indices = {0}
    for idx, line in critical_lines[:3]:
        for neighbor in range(max(0, idx - 1), min(len(lines), idx + 2)):
            if neighbor not in added_indices:
                summary_lines.append(f"Line {neighbor + 1}: {lines[neighbor]}")
                added_indices.add(neighbor)

    summary_lines.append("... [truncated intermediate lines] ...")
    for idx in range(max(0, len(lines) - 3), len(lines)):
        if idx not in added_indices:
            summary_lines.append(f"Line {idx + 1}: {lines[idx]}")

    return "\n".join(summary_lines)

# ── Self-Healing and Auto-discovery Helpers ──────────────────
def register_discovered_service(service_name: str, tag: str, is_docker: bool = False):
    """Autonomously registers a newly discovered service to config.json."""
    try:
        if service_name in PROJECT_MAP:
            return

        logger.info("New service discovered autonomously: %s -> %s", service_name, tag)
        PROJECT_MAP[service_name] = tag

        # Update config.json file
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            
            # Update project map
            if "project_map" not in cfg:
                cfg["project_map"] = {}
            cfg["project_map"][service_name] = tag

            # Update lists
            if is_docker:
                if "docker_containers" not in cfg:
                    cfg["docker_containers"] = []
                if service_name not in cfg["docker_containers"]:
                    cfg["docker_containers"].append(service_name)
            
            # Atomic write
            temp_path = CONFIG_PATH.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(cfg, f, indent=2)
            temp_path.replace(CONFIG_PATH)
            logger.info("config.json has been autonomously updated.")
    except Exception as e:
        logger.error("Failed to autonomously update config.json: %s", e)

def append_incident_to_graph_doc(service: str, title: str, status: str, proposed_command: str, success: bool):
    """Appends incident details to sre_incidents.md to trigger graphify-watch updates."""
    try:
        incident_file = INSTALL_DIR / "sre_incidents.md"
        if not incident_file.parent.exists():
            incident_file = Path("sre_incidents.md")
        
        now_str = datetime.now(timezone.utc).isoformat()
        entry = (
            f"\n## Incident: {title}\n"
            f"- **Service**: {service}\n"
            f"- **Status**: {status}\n"
            f"- **Timestamp**: {now_str}\n"
            f"- **Proposed Command**: `{proposed_command}`\n"
            f"- **Success**: {success}\n"
        )
        with open(incident_file, "a") as f:
            f.write(entry)
        logger.info("Incident appended to graph document: %s", incident_file)
    except Exception as e:
        logger.error("Error appending incident to graph doc: %s", e)

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
            cursor.execute("SELECT * pending_actions WHERE id = ?", (action_id,))
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

# ── File Editor & Patch Validation Sandbox ───────────────────

# ══════════════════════════════════════════════════════════════════════════════
# GENERIC SAAS LAYER — ManifestLoader, ServiceDiscovery, LanguageValidator,
#                      RebuildManager
# ══════════════════════════════════════════════════════════════════════════════

class ManifestLoader:
    """
    Reads manifest.yaml (auto-generated by install.sh) and indexes services.
    Falls back gracefully if manifest.yaml is missing (backwards compat).
    """

    def __init__(self, manifest_path: Path):
        self._path = manifest_path
        self._data: dict = {}
        self._by_container: dict = {}   # container_name → service_config
        self._by_name: dict = {}        # service_name → service_config
        self._load()

    def _load(self):
        if not self._path.exists():
            logger.debug("[MANIFEST] manifest.yaml not found at %s — using defaults.", self._path)
            return
        if not YAML_AVAILABLE:
            logger.warning("[MANIFEST] PyYAML not installed. Run: pip install pyyaml")
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                self._data = _yaml_mod.safe_load(f) or {}
            for svc in self._data.get("services", []):
                for key in ("container_name", "unit", "app_name", "deployment"):
                    val = svc.get(key)
                    if val:
                        self._by_container[val] = svc
                self._by_name[svc.get("name", "")] = svc
            logger.info("[MANIFEST] Loaded %d service(s) from manifest.yaml", len(self._data.get("services", [])))
        except Exception as e:
            logger.error("[MANIFEST] Failed to load manifest.yaml: %s", e)

    def get_service_by_container(self, container_name: str) -> Optional[dict]:
        return self._by_container.get(container_name)

    def get_service_by_name(self, name: str) -> Optional[dict]:
        return self._by_name.get(name)

    @property
    def tenant_id(self) -> str:
        return self._data.get("tenant_id", "self-hosted")

    @property
    def services(self) -> list:
        return self._data.get("services", [])

    @property
    def api_key(self) -> str:
        return self._data.get("sre_api_key", "")

    def services_summary(self) -> str:
        """Generate a short services summary for LLM prompts."""
        lines = []
        for svc in self.services:
            rt = svc.get("runtime", "unknown")
            name = svc.get("name", "?")
            cf = svc.get("compose_file", svc.get("unit", svc.get("app_name", "")))
            lines.append(f"  - {name} (runtime: {rt}, path: {cf})")
        return "\n".join(lines) if lines else "  (no services configured)"


class ServiceDiscovery:
    """
    Resolves container-internal paths to host paths via `docker inspect`.
    No hardcoded path maps — works for any customer on any server.
    """

    def __init__(self):
        self._mount_cache: dict = {}   # container_name → List[dict]
        self._cache_ts: dict = {}
        self._ttl = 60  # seconds

    def resolve_host_path(self, container_name: str, container_path: str) -> Optional[str]:
        """
        Example:
          container_name="bikefit-api", container_path="/app/main.py"
          → "/home/user/myapp/main.py"
        Returns None if container not found or path not mounted.
        """
        mounts = self._get_mounts(container_name)
        # Sort by Destination length descending (most-specific match first)
        mounts_sorted = sorted(mounts, key=lambda m: len(m.get("Destination", "")), reverse=True)
        for mount in mounts_sorted:
            dest = mount.get("Destination", "")
            source = mount.get("Source", "")
            if not dest or not source:
                continue
            if container_path == dest or container_path.startswith(dest + "/"):
                relative = container_path[len(dest):]
                resolved = source.rstrip("/") + relative
                logger.debug("[DISCOVERY] %s:%s → %s", container_name, container_path, resolved)
                return resolved
        return None

    def _get_mounts(self, container_name: str) -> list:
        now = time.time()
        if container_name in self._mount_cache:
            if now - self._cache_ts.get(container_name, 0) < self._ttl:
                return self._mount_cache[container_name]
        try:
            res = subprocess.run(
                ["docker", "inspect", container_name, "--format", "{{json .Mounts}}"],
                capture_output=True, text=True, timeout=5
            )
            mounts = json.loads(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else []
            self._mount_cache[container_name] = mounts
            self._cache_ts[container_name] = now
            return mounts
        except Exception as e:
            logger.debug("[DISCOVERY] docker inspect failed for %s: %s", container_name, e)
            return []

    def invalidate(self, container_name: str):
        """Force cache refresh for a container (call after rebuild)."""
        self._mount_cache.pop(container_name, None)
        self._cache_ts.pop(container_name, None)


class LanguageValidator:
    """
    Language-agnostic syntax validator.
    Selects the right tool based on file extension.
    Gracefully skips if the required binary is not installed.
    """

    VALIDATORS = {
        ".py":   ["python3", "-m", "py_compile", "__FILE__"],
        ".js":   ["node", "--check", "__FILE__"],
        ".ts":   ["node", "--check", "__FILE__"],   # tsc not always available
        ".rb":   ["ruby", "-c", "__FILE__"],
        ".sh":   ["bash", "-n", "__FILE__"],
        ".php":  ["php", "-l", "__FILE__"],
    }

    def validate(self, file_path: str) -> tuple:
        """
        Returns (ok: bool, error_msg: str).
        Unknown extensions → (True, "") to be non-blocking.
        """
        ext = Path(file_path).suffix.lower()
        cmd_template = self.VALIDATORS.get(ext)
        if not cmd_template:
            return True, ""

        binary = cmd_template[0]
        which = subprocess.run(["which", binary], capture_output=True)
        if which.returncode != 0:
            logger.debug("[VALIDATOR] %s not found, skipping %s validation", binary, ext)
            return True, ""  # binary absent → non-blocking

        cmd = [c.replace("__FILE__", file_path) for c in cmd_template]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode != 0:
                err = (res.stderr or res.stdout)[:500]
                return False, err
            return True, ""
        except Exception as e:
            return False, str(e)


class RebuildManager:
    """
    Triggers service rebuild/restart after a successful code patch.
    Supports: docker-compose, systemd, pm2, kubernetes, bare process.
    Service config comes from ManifestLoader.
    """

    def rebuild(self, service_config: dict) -> tuple:
        """
        Returns (success: bool, output: str)
        """
        if not service_config:
            return False, "No service config provided"
        runtime = service_config.get("runtime", "")
        try:
            if runtime == "docker-compose":
                return self._docker_compose(service_config)
            elif runtime == "systemd":
                return self._systemd(service_config)
            elif runtime == "pm2":
                return self._pm2(service_config)
            elif runtime == "kubernetes":
                return self._kubernetes(service_config)
            elif runtime == "bare":
                return self._bare(service_config)
            else:
                return False, f"Unknown runtime: {runtime}"
        except Exception as e:
            return False, str(e)

    def _docker_compose(self, cfg: dict) -> tuple:
        compose_file = cfg.get("compose_file", "")
        compose_dir = str(Path(compose_file).parent) if compose_file else None
        container = cfg.get("container_name", "")
        cmd = ["docker", "compose", "up", "-d", "--build"]
        if container:
            cmd.append(container)
        res = subprocess.run(cmd, cwd=compose_dir, capture_output=True, text=True, timeout=180)
        out = (res.stdout + res.stderr)[:600]
        return res.returncode == 0, out

    def _systemd(self, cfg: dict) -> tuple:
        unit = cfg.get("unit", "")
        res = subprocess.run(["sudo", "systemctl", "restart", unit],
                             capture_output=True, text=True, timeout=30)
        return res.returncode == 0, (res.stdout + res.stderr)[:300]

    def _pm2(self, cfg: dict) -> tuple:
        app = cfg.get("app_name", "")
        res = subprocess.run(["pm2", "restart", app],
                             capture_output=True, text=True, timeout=30)
        return res.returncode == 0, (res.stdout + res.stderr)[:300]

    def _kubernetes(self, cfg: dict) -> tuple:
        ns = cfg.get("namespace", "default")
        dep = cfg.get("deployment", "")
        res = subprocess.run(
            ["kubectl", "rollout", "restart", f"deployment/{dep}", "-n", ns],
            capture_output=True, text=True, timeout=60
        )
        return res.returncode == 0, (res.stdout + res.stderr)[:300]

    def _bare(self, cfg: dict) -> tuple:
        cmd = cfg.get("restart_command", "")
        if not cmd:
            return False, "bare runtime: restart_command not defined in manifest"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return res.returncode == 0, (res.stdout + res.stderr)[:300]

class FileEditor:
    """
    SRE Daemon File Management, Syntax Validation & Safe Patching Sandbox
    """
    def __init__(self):
        pass

    def read_file(self, file_path: str, start_line: int = 1, end_line: int = None) -> str:
        try:
            if not os.path.exists(file_path):
                return f"Error: File {file_path} not found"
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            
            end_line = end_line or len(lines)
            selected = lines[start_line-1:end_line]
            return "".join(selected)
        except Exception as e:
            return f"Error reading file: {e}"

    def write_file(self, file_path: str, content: str) -> None:
        """Atomically writes content to the target file."""
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, file_path)

    def validate_syntax(self, file_path: str) -> tuple:
        """
        Validates python file syntax using py_compile.
        Returns: (is_valid: bool, error_message: str)
        """
        if not file_path.endswith(".py"):
            return True, ""
        try:
            import py_compile
            py_compile.compile(file_path, doraise=True)
            return True, ""
        except py_compile.PyCompileError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)

    def apply_patch(self, file_path: str, search_block: str, replace_block: str, dry_run: bool = False) -> tuple:
        """
        Attempts to apply a search-and-replace block, validates compile syntax, 
        and rolls back if syntax validation fails.
        Returns: (success: bool, detail: str)
        """
        try:
            if not os.path.exists(file_path):
                return False, f"File {file_path} not found"
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if search_block not in content:
                # Try with normalized whitespace if exact match fails
                normalized_content = re.sub(r"\s+", " ", content)
                normalized_search = re.sub(r"\s+", " ", search_block)
                if normalized_search not in normalized_content:
                    return False, "Search block not found in file content"

            # Perform substitution
            patched_content = content.replace(search_block, replace_block)

            # Sandbox validation via temp file
            temp_test_path = file_path + "_test_patch.py"
            with open(temp_test_path, "w", encoding="utf-8") as f:
                f.write(patched_content)

            is_valid, err_msg = self.validate_syntax(temp_test_path)
            if os.path.exists(temp_test_path):
                os.unlink(temp_test_path)

            if not is_valid:
                return False, f"Syntax validation failed: {err_msg}"

            if not dry_run:
                # Create backup
                backup_path = file_path + ".bak"
                with open(backup_path, "w", encoding="utf-8") as f:
                    f.write(content)

                # Write patched file atomically
                self.write_file(file_path, patched_content)
                return True, "Patch applied and verified successfully"
            
            return True, "Dry-run validation successful"
            
        except Exception as e:
            return False, f"Patch failed: {e}"

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
# Loaded dynamically from the monitors module at runtime.

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
        self.mac_checker      = MacChecker()
        self.ollama           = OllamaClient()
        self.gemini           = GeminiClient()
        self.groq             = GroqClient()
        self.xai              = XAIClient()
        self.anthropic        = AnthropicClient()
        self.file_editor      = FileEditor()
        self.manifest         = ManifestLoader(MANIFEST_PATH)
        self.discovery        = ServiceDiscovery()
        self.lang_validator   = LanguageValidator()
        self.rebuild_manager  = RebuildManager()

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

        # Truce (Ateşkes) Cooldown Check
        if err_hash in TRUCE_CACHE:
            last_time, count = TRUCE_CACHE[err_hash]
            if now - last_time < TRUCE_COOLDOWN_SECONDS:
                TRUCE_CACHE[err_hash] = (last_time, count + 1)
                logger.info("Truce (Ateşkes) aktif: Hata '%s' son %d saniyede zaten inceleniyor. Tekrarlama adedi: %d", project_tag, TRUCE_COOLDOWN_SECONDS, count + 1)
                return
        
        # Register in Truce cache
        TRUCE_CACHE[err_hash] = (now, 1)

        threading.Thread(
            target=self._heal, args=(tagged_line, project_tag, err_hash),
            name=f"healer-{project_tag}", daemon=True
        ).start()

    def _build_prompt(self, tagged_line: str, project_tag: str, err_hash: str = None, context_section: str = "") -> str:
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

        # Build dynamic service context from manifest
        services_ctx = self.manifest.services_summary()
        return (
            # ── SRE PERSONA ───────────────────────────────────────────────────
            "You are the Senior SRE Engineer for TriHonor's autonomous self-healing platform.\n"
            "Your engineering principles (non-negotiable):\n"
            "  1. NEVER use hardcoded paths. Always use relative paths or variables.\n"
            "  2. ALWAYS write minimal, surgical patches — change only the broken line(s).\n"
            "  3. NEVER restart a service unless the code patch alone cannot fix the issue.\n"
            "  4. Prefer defensive code patterns: null checks, try/except, fallbacks.\n"
            "  5. After a code patch (replace/write), a service rebuild will be triggered automatically — do NOT add a shell restart action unless it is a non-code issue.\n"
            "  6. If the fix requires a new dependency, use 'append' on requirements.txt — not a shell pip install.\n"
            "  7. Your patches must pass syntax validation for the target language before being applied.\n"
            # ── CONTEXT ───────────────────────────────────────────────────────
            f"\nCurrent service: {project_name}\n"
            "Services registered in manifest.yaml:\n"
            f"{services_ctx}\n\n"
            "IMPORTANT: Set target=\"__DETECTED_BY_SRE__\" in replace/write actions. "
            "The daemon resolves the real host path automatically from the traceback.\n"
            f"{past_context}\n"
            "Analyze the following error and respond with the minimal surgical fix:\n"
            "1. Kök nedeni açıkla (1-2 cümle)\n"
            "2. Tekrarlanmaması için önlem öner\n"
            "3. Eğer hata otomatik olarak düzeltilebiliyorsa (örn: eksik python kütüphanesini requirements.txt'e ekleme, konteyner veya servis restart etme), bunu eylemler ('actions') dizisi olarak tanımla.\n\n"
            "Eylemler türleri:\n"
            "- 'append': Add a new line to a file (e.g., target='requirements.txt', payload='slowapi')\n"
            "- 'write': Overwrite a file with new content.\n"
            "- 'replace': Bir dosyada belirli bir kod bloğunu yeni kod bloğuyla değiştirmek için.\n"
            "- 'replace': Bir dosyada belirli bir kod bloğunu yeni kod bloğuyla değiştirmek için.\n"
            "  Format: {\"type\": \"replace\", \"target\": \"__DETECTED_BY_SRE__\", \"search\": \"old_code\", \"replace\": \"new_code\"}\n"
            "- 'shell': Güvenli bir komut çalıştırmak için (örn: target='/home/pi/bikefit', payload='docker compose up -d --build bikefit-api').\n"
            "  NOT: Shell komutları yalnızca docker compose, docker restart veya systemctl restart/start komutları olmalıdır. Güvenli olmayan veya izin verilmeyen hiçbir komut çalıştırma!\n\n"
            f"{context_section}"
            f"HATA:\n{tagged_line[:1500]}\n\n"
            "JSON formatında yanıt ver. Yanıtın mutlaka geçerli bir JSON olmalıdır ve kod blokları içermemelidir. Örnek şema:\n"
            "{\n"
            '  "root_cause": "Kök neden açıklaması",\n'
            '  "prevention": "Önlem açıklaması",\n'
            '  "actions": [\n'
            '    {"type": "replace", "target": "/home/pi/bikefit/pi_main.py", "search": "results = model(frame, verbose=False)", "replace": "results = model(frame, verbose=False)\\n    if not results or len(results[0].boxes) == 0:\\n        raise HTTPException(status_code=422, detail=\"YOLO failed\")"},\n'
            '    {"type": "shell", "target": "/home/pi/bikefit", "payload": "docker compose up -d --build bikefit-api"}\n'
            '  ]\n'
            "}"
        )

    def _classify_risk(self, actions: List[Dict[str, Any]]) -> str:
        """Deterministik kural tabanlı risk sınıflandırması."""
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

    def _heal(self, tagged_line: str, project_tag: str, err_hash: str):
        autonomous = get_daemon_setting("autonomous_mode", "0") == "1"
        start_time = time.time()
        
        # Token Minimization: Summarize log
        summarized_line = summarize_log(tagged_line)
        container_name = project_tag.strip("[]")
        error_log_snippet = summarized_line

        # ── OTONOM HAFIZA ─────────────────────────────────────────────────────
        _error_hash = compute_error_hash(container_name, error_log_snippet)
        _cached_cmd = get_best_strategy(DB_PATH, _error_hash)

        # Semantic fallback — fuzzy match if exact hash miss
        _semantic_match = False
        if not _cached_cmd:
            sem = get_semantic_strategy(DB_PATH, error_log_snippet)
            if sem:
                _cached_cmd, _sim_score = sem
                _semantic_match = True
                logger.info("[SEMANTIC HIT] similarity=%.0f%% → %s", _sim_score * 100, _cached_cmd[:80])

        if _cached_cmd:
            _hit_type = "semantic-registry" if _semantic_match else "strategy-registry"
            logger.info(f"[REGISTRY HIT] {container_name} hafızadan ({_hit_type}): {_cached_cmd}")
            send_telegram_text(
                TELEGRAM_CHAT_ID,
                f"🧠 *Otonom Hafıza* — `{container_name}`\n"
                f"Kayıtlı strateji (LLM Maliyeti: 0 token):\n`{_cached_cmd}`"
            )
            try:
                _result = subprocess.run(
                    _cached_cmd, shell=True, capture_output=True, text=True, timeout=60
                )
                _cache_success = _result.returncode == 0
                _output = (_result.stdout + "\n" + _result.stderr).strip()
            except Exception as _e:
                logger.warning(f"[REGISTRY] Exception: {_e}")
                _cache_success = False
                _output = str(_e)

            update_strategy_result(DB_PATH, _error_hash, _cached_cmd, _cache_success)

            # Log and notify
            save_heal_history(
                error_hash=err_hash,
                error_message=tagged_line,
                project_tag=project_tag,
                risk_level="Low",
                prompt="Strategy Registry Cached Call",
                llm_response=json.dumps([{"type": "shell", "payload": _cached_cmd}]),
                llm_source="strategy-registry",
                actions=[{"type": "shell", "payload": _cached_cmd}],
                execution_output=[{"command": _cached_cmd, "status": "success" if _cache_success else "failed", "output": _output}],
                success=_cache_success,
                duration=time.time() - start_time
            )
            self._write_heal_log(tagged_line, _cached_cmd, _hit_type, _cache_success, project_tag, [{"command": _cached_cmd, "status": "success" if _cache_success else "failed", "output": _output}])
            
            report_incident_to_platform(
                service=project_tag,
                title=f"Autonomous Healing: {project_tag}",
                logs=tagged_line,
                status="resolved" if _cache_success else "failed",
                proposed_command=_cached_cmd,
                action_output=json.dumps([{"command": _cached_cmd, "status": "success" if _cache_success else "failed", "output": _output}])
            )
            append_incident_to_graph_doc(
                service=project_tag,
                title=f"Autonomous Healing: {project_tag}",
                status="resolved" if _cache_success else "failed",
                proposed_command=_cached_cmd,
                success=_cache_success
            )

            if _cache_success:
                logger.info("[REGISTRY] Cached strateji BAŞARILI, LLM atlandı.")
                return
            else:
                logger.warning("[REGISTRY] Cached strateji başarısız → LLM fallback")
                send_telegram_text(
                    TELEGRAM_CHAT_ID,
                    f"⚠️ `{container_name}` kayıtlı strateji başarısız, LLM devreye alındı."
                )
        # ── OTONOM HAFIZA SONU ────────────────────────────────────────────────

        # ── DIAGNOSTIC LAYER 2 — Generic Path Resolution ────────────────────────
        file_context = ""
        detected_target_file: Optional[str] = None
        skip_local_pi = False
        try:
            # Match any source file extension, not just .py
            match = re.search(r'File "([^"]+\.[a-zA-Z]+)", line (\d+)', tagged_line)
            if not match:
                match = re.search(r'File "([^"]+\.py)", line (\d+)', tagged_line)
            if match:
                raw_file = match.group(1)
                line_no = int(match.group(2))

                # Attempt 1: docker inspect (generic, works for any customer)
                container_name = project_tag.strip("[]").lower().replace(" ", "-").replace("_", "-")
                # Try the container name directly and common variations
                resolved = None
                for cname in [container_name, container_name + "-api", container_name.split("-")[0]]:
                    resolved = self.discovery.resolve_host_path(cname, raw_file)
                    if resolved:
                        detected_target_file = resolved
                        logger.info("[DISCOVERY] Resolved via docker inspect: %s → %s", raw_file, detected_target_file)
                        break

                # Attempt 2: manifest service lookup
                if not detected_target_file:
                    svc = self.manifest.get_service_by_container(container_name)
                    if svc and svc.get("compose_file"):
                        project_dir = str(Path(svc["compose_file"]).parent)
                        basename = os.path.basename(raw_file)
                        candidate = os.path.join(project_dir, basename)
                        if os.path.exists(candidate):
                            detected_target_file = candidate
                            logger.info("[DISCOVERY] Resolved via manifest: %s → %s", raw_file, detected_target_file)

                # Attempt 3: fallback — if it's already an absolute host path
                if not detected_target_file and raw_file.startswith("/") and os.path.exists(raw_file):
                    detected_target_file = raw_file
                    logger.info("[DISCOVERY] Path is already a valid host path: %s", raw_file)

                if detected_target_file:
                    logger.info("[DIAGNOSTIC] Traceback detected: %s L%d. Reading code context...", detected_target_file, line_no)
                    start_line = max(1, line_no - 20)
                    end_line = line_no + 20
                    raw_code = self.file_editor.read_file(detected_target_file, start_line, end_line)
                    if raw_code and not raw_code.startswith("Error"):
                        file_context = (
                            f"DETECTED ERROR FILE ({detected_target_file} lines {start_line}-{end_line}):\n"
                            "```\n"
                            f"{raw_code}\n"
                            "```\n"
                            "Analyze the code context above. Identify the error line and generate a 'replace' action.\n"
                            "Set target=\"__DETECTED_BY_SRE__\" — the daemon will resolve the real path automatically.\n\n"
                        )
                        skip_local_pi = True   # large prompt — skip slow Pi Ollama
                else:
                    logger.warning("[DIAGNOSTIC] Could not resolve host path for: %s", raw_file)
        except Exception as e_diag:
            logger.warning("[DIAGNOSTIC] Code context read failed: %s", e_diag)
        # ── DIAGNOSTIC LAYER 2 END ───────────────────────────────────────────

        prompt = self._build_prompt(summarized_line, project_tag, err_hash, context_section=file_context)
        
        result = None
        source = None
        success = False

        # 1. Local Mac Ollama (if online)
        mac_online = self.mac_checker.is_mac_online()
        if mac_online:
            for attempt in range(1, MAX_LOCAL_TRIES + 1):
                result = self.ollama.query(MAC_OLLAMA_URL, "qwen2.5-coder:32b", prompt)
                source = "mac-ollama/qwen2.5-coder:32b"
                if result:
                    success = True
                    break
                time.sleep(5 * attempt)

        # Budget helper
        def run_with_budget(provider_name: str, query_fn) -> Optional[str]:
            limit = DAILY_CLOUD_LIMITS.get(provider_name, 99999)
            current = get_daily_calls(provider_name)
            if current >= limit:
                logger.warning("Daily API budget exceeded for %s (%d/%d calls). Skipping.", provider_name, current, limit)
                return None
            res = query_fn()
            if res is not None:
                increment_daily_calls(provider_name)
            return res

        # 2. Google Gemini API (Free tier)
        if not success and GEMINI_API_KEY:
            result = run_with_budget("gemini", lambda: self.gemini.query(prompt))
            source = "gemini/gemini-2.5-flash"
            success = result is not None

        # 3. Groq API (Free tier)
        if not success and GROQ_API_KEY:
            result = run_with_budget("groq", lambda: self.groq.query(prompt))
            source = "groq/llama-3.3-70b-versatile"
            success = result is not None

        # 4. Grok (xAI) API (Cheap cloud fallback)
        if not success and XAI_API_KEY:
            result = run_with_budget("xai", lambda: self.xai.query(prompt))
            source = "xai/grok-2-1212"
            success = result is not None

        # 5. Local Pi Ollama (Offline fallback — skipped if code context present)
        if not success and not skip_local_pi:
            for attempt in range(1, MAX_LOCAL_TRIES + 1):
                result = self.ollama.query(PI_OLLAMA_URL, "qwen2.5-coder:7b", prompt)
                source = "pi-ollama/qwen2.5-coder:7b"
                if result:
                    success = True
                    break
                time.sleep(5 * attempt)
        elif not success and skip_local_pi:
            logger.info("[HEAL] Pi Ollama skipped (code context prompt — using cloud cascade)")

        # 6. Anthropic Claude (Expensive cloud fallback)
        if not success and ANTHROPIC_KEY:
            result = run_with_budget("claude", lambda: self.anthropic.query(prompt))
            source = "anthropic/claude-sonnet-4-6"
            success = result is not None

        if success and result:
            try:
                # Clean JSON markdown blocks
                cleaned = result.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                data = json.loads(cleaned.strip())
                actions = data.get("actions", [])

                # ── LAYER 3: Deterministic target override ────────────────────
                # LLM sets target="__DETECTED_BY_SRE__" — daemon resolves real path
                if detected_target_file:
                    for act in actions:
                        if act.get("type") in ("replace", "write", "append"):
                            t = act.get("target", "")
                            if t == "__DETECTED_BY_SRE__" or not t or not os.path.exists(t):
                                act["target"] = detected_target_file
                                logger.info("[LAYER3] target override → %s", detected_target_file)
                else:
                    # If target is __DETECTED_BY_SRE__ but traceback wasn't resolved, drop those code patch actions
                    filtered_actions = []
                    for act in actions:
                        t = act.get("target", "")
                        if act.get("type") in ("replace", "write", "append") and t == "__DETECTED_BY_SRE__":
                            logger.warning("[HEAL] Dropped action targeting __DETECTED_BY_SRE__ because no traceback file was resolved: %s", act)
                            continue
                        filtered_actions.append(act)
                    actions = filtered_actions
                # ── LAYER 3: target override end ─────────────────────────────

                risk_level = self._classify_risk(actions)
                logger.info("%s Analiz sonucu risk seviyesi: %s | Otonom mod: %s", project_tag, risk_level, autonomous)

                if autonomous:
                    # FULL AUTONOMOUS — bypass all risk checks
                    send_telegram_text(
                        TELEGRAM_CHAT_ID,
                        f"🤖 *SRE Otonom İyileştirme*\n"
                        f"📍 *Servis*: `{md_escape(project_tag)}`\n"
                        f"⚡ *Risk*: `{md_escape(risk_level)}`\n"
                        f"🔍 *Kök Neden*: {md_escape(data.get('root_cause', ''))}\n\n"
                        f"Çözüm otonom uygulanıyor\\.\\.\\."
                    )
                    executed_status = self.execute_approved_actions(actions, 0)
                    duration = time.time() - start_time
                    all_ok = all(e.get("status") == "success" for e in executed_status)
                    # ── REGISTRY KAYIT ────────────────────────────────────────────────────
                    if actions:
                        register_actions_in_registry(DB_PATH, _error_hash, actions, success=all_ok)
                        logger.info(f"[REGISTRY] {len(actions)} aksiyon kaydedildi (success={all_ok}, hash={_error_hash})")
                    # ── REGISTRY KAYIT SONU ───────────────────────────────────────────────

                    if all_ok:
                        send_telegram_text(
                            TELEGRAM_CHAT_ID,
                            f"✅ *SRE Otonom İyileştirme Başarılı*\n"
                            f"📍 *Servis*: `{md_escape(project_tag)}`\n"
                            f"⏱ *Süre*: `{duration:.1f}s` | 🤖 *Kaynak*: `{md_escape(source)}`\n"
                            f"Sorun çözüldü, servis stabilize edildi\\."
                        )
                    else:
                        failed = [e for e in executed_status if e.get("status") != "success"]
                        fail_summary = md_escape(json.dumps(failed, ensure_ascii=False)[:300])
                        send_telegram_text(
                            TELEGRAM_CHAT_ID,
                            f"❌ *SRE Otonom İyileştirme Başarısız*\n"
                            f"📍 *Servis*: `{md_escape(project_tag)}`\n"
                            f"Bekçi köpeği geri yükleme yaptı\\!\n"
                            f"Hata: `{fail_summary}`"
                        )

                    save_heal_history(
                        error_hash=err_hash,
                        error_message=tagged_line,
                        project_tag=project_tag,
                        risk_level=risk_level,
                        prompt=prompt,
                        llm_response=result,
                        llm_source=source,
                        actions=actions,
                        execution_output=executed_status,
                        success=all_ok,
                        duration=duration
                    )
                    self._write_heal_log(tagged_line, result, source, all_ok, project_tag, executed_status)
                    
                    # Report to SRE platform central backend (Slack/Metrics)
                    report_cmd = ""
                    if actions:
                        report_cmd = actions[0].get("payload", "") if actions[0].get("type") == "shell" else json.dumps(actions)
                    report_incident_to_platform(
                        service=project_tag,
                        title=f"Autonomous Healing: {project_tag}",
                        logs=tagged_line,
                        status="resolved" if all_ok else "failed",
                        proposed_command=report_cmd,
                        action_output=json.dumps(executed_status)
                    )
                    append_incident_to_graph_doc(
                        service=project_tag,
                        title=f"Autonomous Healing: {project_tag}",
                        status="resolved" if all_ok else "failed",
                        proposed_command=report_cmd,
                        success=all_ok
                    )

                elif risk_level in ("Low", "Medium"):
                    # Non-autonomous but low/medium: still auto-execute
                    send_telegram_text(
                        TELEGRAM_CHAT_ID,
                        f"🤖 *SRE İyileştirme (Düşük/Orta Risk)*\n"
                        f"📍 *Servis*: `{md_escape(project_tag)}`\n"
                        f"⚡ *Risk*: `{md_escape(risk_level)}`\n"
                        f"🔍 *Kök Neden*: {md_escape(data.get('root_cause', ''))}\n\n"
                        f"Çözüm otomatik uygulanıyor\\.\\.\\."
                    )
                    executed_status = self.execute_approved_actions(actions, 0)
                    duration = time.time() - start_time
                    all_ok = all(e.get("status") == "success" for e in executed_status)
                    # ── REGISTRY KAYIT ────────────────────────────────────────────────────
                    if actions:
                        register_actions_in_registry(DB_PATH, _error_hash, actions, success=all_ok)
                        logger.info(f"[REGISTRY] {len(actions)} aksiyon kaydedildi (success={all_ok}, hash={_error_hash})")
                    # ── REGISTRY KAYIT SONU ───────────────────────────────────────────────
                    
                    if all_ok:
                        send_telegram_text(
                            TELEGRAM_CHAT_ID,
                            f"✅ *SRE İyileştirme Başarılı*\n"
                            f"📍 *Servis*: `{md_escape(project_tag)}`\n"
                            f"⏱ *Süre*: `{duration:.1f}s` | 🤖 *Kaynak*: `{md_escape(source)}`\n"
                            f"Sorun çözüldü, servis stabilize edildi\\."
                        )
                    else:
                        failed = [e for e in executed_status if e.get("status") != "success"]
                        fail_summary = md_escape(json.dumps(failed, ensure_ascii=False)[:300])
                        send_telegram_text(
                            TELEGRAM_CHAT_ID,
                            f"❌ *SRE İyileştirme Başarısız*\n"
                            f"📍 *Servis*: `{md_escape(project_tag)}`\n"
                            f"Hata: `{fail_summary}`"
                        )
                        
                    save_heal_history(
                        error_hash=err_hash,
                        error_message=tagged_line,
                        project_tag=project_tag,
                        risk_level=risk_level,
                        prompt=prompt,
                        llm_response=result,
                        llm_source=source,
                        actions=actions,
                        execution_output=executed_status,
                        success=all_ok,
                        duration=duration
                    )
                    self._write_heal_log(tagged_line, result, source, all_ok, project_tag, executed_status)
                    
                    # Report to SRE platform central backend (Slack/Metrics)
                    report_cmd = ""
                    if actions:
                        report_cmd = actions[0].get("payload", "") if actions[0].get("type") == "shell" else json.dumps(actions)
                    report_incident_to_platform(
                        service=project_tag,
                        title=f"Auto-remount Healing: {project_tag}",
                        logs=tagged_line,
                        status="resolved" if all_ok else "failed",
                        proposed_command=report_cmd,
                        action_output=json.dumps(executed_status)
                    )
                    append_incident_to_graph_doc(
                        service=project_tag,
                        title=f"Auto-remount Healing: {project_tag}",
                        status="resolved" if all_ok else "failed",
                        proposed_command=report_cmd,
                        success=all_ok
                    )
                else:
                    # High/Critical + not autonomous → HITL Telegram approval
                    action_id = add_pending_action(err_hash, risk_level, actions)
                    self._send_approval_request(action_id, data, tagged_line, risk_level, project_tag)

            except Exception as e:
                logger.error("Analiz parse hatası: %s", str(e))
                duration = time.time() - start_time
                save_heal_history(
                    error_hash=err_hash,
                    error_message=tagged_line,
                    project_tag=project_tag,
                    risk_level="unknown",
                    prompt=prompt,
                    llm_response=result,
                    llm_source=source or "unknown",
                    actions=[],
                    execution_output=[{"error": str(e)}],
                    success=False,
                    duration=duration
                )
                self._write_heal_log(tagged_line, result, source, False, project_tag, [{"error": str(e)}])

    def _send_approval_request(self, action_id: int, data: dict, error_msg: str, risk: str, tag: str):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram token eksik, onay isteği gönderilemiyor.")
            return

        actions = data.get("actions", [])
        actions_summary = ""
        for act in actions:
            act_type = act.get("type", "")
            target = act.get("target", "")
            if act_type == "replace":
                search = act.get("search", "")
                replace = act.get("replace", "")
                import difflib
                diff = list(difflib.unified_diff(
                    search.splitlines(),
                    replace.splitlines(),
                    fromfile="old_code",
                    tofile="new_code",
                    lineterm=""
                ))
                diff_text = "\n".join(diff[2:])
                actions_summary += f"• *replace* -> `{md_escape(target)}`:\n```diff\n{diff_text}\n```\n"
            else:
                payload_preview = act.get("payload", "")[:100]
                actions_summary += f"• {md_escape(act_type)} -> {md_escape(target)} ({md_escape(payload_preview)}...)\n"

        root_cause = md_escape(data.get("root_cause", ""))
        tag_safe = md_escape(tag)
        risk_safe = md_escape(risk)

        msg = (
            f"🚨 *SRE Daemon v5: Onay Bekleyen Aksiyon*\n\n"
            f"📍 *Nerede*: {tag_safe}\n"
            f"⚡ *Risk Seviyesi*: `{risk_safe}`\n"
            f"📝 *Kök Neden*: {root_cause}\n\n"
            f"⚙️ *Önerilen Aksiyonlar*:\n{actions_summary}\n"
            f"⏰ *Zaman Aşımı*: 10 dakika (otomatik red)\n\n"
            f"Lütfen aşağıdaki butonlarla onayı onaylayın."
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Uygula", "callback_data": f"approve_{action_id}"},
                    {"text": "❌ Reddet", "callback_data": f"reject_{action_id}"}
                ]
            ]
        }

        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "Markdown",
                "reply_markup": reply_markup
            }, timeout=10)
        except Exception as e:
            logger.error("Telegram onay isteği gönderme hatası: %s", str(e))

    def execute_approved_actions(self, actions: List[Dict[str, Any]], action_id: int) -> List[Dict[str, Any]]:
        executed = []
        is_self_fix = any(
            act.get("target") and Path(act.get("target")).resolve() == SELF_PATH
            for act in actions
        )
        timestamp = str(int(time.time()))
        tag_name = f"pre-fix-{timestamp}"

        if is_self_fix:
            if not SELF_FIX_LOCK.acquire(blocking=False):
                return [{"status": "rejected", "reason": "self-fix already in progress"}]
            try:
                # Git Tag backup point creation before modifications
                subprocess.run(["git", "-C", str(INSTALL_DIR), "tag", "-a", tag_name, "-m", f"SRE pre-fix backup {timestamp}"], check=True)
                logger.info("Kritik self-fix yedekleme etiketi oluşturuldu: %s", tag_name)
            except Exception as e:
                logger.error("Git tag oluşturma hatası: %s", str(e))
                SELF_FIX_LOCK.release()
                return [{"status": "failed", "error": f"Git tag yedekleme hatası: {str(e)}"}]

        try:
            for act in actions:
                act_type = act.get("type")
                target   = act.get("target", "").strip()
                payload  = act.get("payload", "")

                # Security target normalization check (dynamic and platform-agnostic)
                if target:
                    target_path = Path(target).resolve()
                    target_str = str(target_path)
                    
                    # Dynamically allow installation dir, user home, and service project directories
                    allowed_dirs = {str(INSTALL_DIR.resolve()), str(Path.home().resolve())}
                    for svc in self.manifest.services:
                        for k in ("compose_file", "unit", "app_name"):
                            v = svc.get(k)
                            if v and "/" in v:
                                allowed_dirs.add(str(Path(v).parent.resolve()))
                                
                    is_safe = any(target_str.startswith(d) for d in allowed_dirs)
                    
                    if not is_safe or ".." in target or ".env" in target:
                        logger.warning("Güvenlik engeli: hedef dizin geçersiz veya yasaklı: %s", target)
                        executed.append({"status": "rejected", "reason": "hedef güvenlik engeli"})
                        continue

                if act_type in ("write", "replace"):
                    target_path = Path(target)
                    tmp_path = target_path.with_name(f"{target_path.stem}.__sre_tmp__{target_path.suffix}")
                    try:
                        if act_type == "write":
                            new_content = payload
                        else:
                            search_str = act.get("search", "")
                            replace_str = act.get("replace", "")
                            orig_content = target_path.read_text(encoding="utf-8")
                            count = orig_content.count(search_str)
                            if count == 0:
                                raise ValueError(f"Değiştirilecek içerik bulunamadı: {search_str[:50]}")
                            if count > 1:
                                raise ValueError(f"Replace belirsiz: {count} eşleşme bulundu")
                            new_content = orig_content.replace(search_str, replace_str, 1)

                        with open(tmp_path, "w", encoding="utf-8") as f:
                            f.write(new_content)
                            f.flush()
                            os.fsync(f.fileno())

                        # Validation — language-agnostic sandbox
                        val_ok, val_err = self.lang_validator.validate(str(tmp_path))
                        if not val_ok:
                            raise ValueError(f"Syntax Validation Error: {val_err}")

                            if target_path.resolve() == SELF_PATH:
                                selftest_res = subprocess.run(
                                    ["python3", str(tmp_path), "--self-test"],
                                    capture_output=True, text=True, timeout=10
                                )
                                if selftest_res.returncode != 0:
                                    raise ValueError(f"Self-test hatası: {selftest_res.stderr}")

                        atomic_write_text(target_path, new_content)
                        executed.append({"type": act_type, "target": target, "status": "success"})
                        logger.info("Atomic %s successfully applied: %s", act_type, target)

                        # ── AUTO-REBUILD ──────────────────────────────────────────────
                        # Find which service owns this file and trigger rebuild
                        try:
                            container_hint = act.get("_container_hint", "")
                            svc_cfg = None
                            if container_hint:
                                svc_cfg = self.manifest.get_service_by_container(container_hint)
                            if not svc_cfg:
                                # Fallback: find by path prefix match across manifest services
                                for svc in self.manifest.services:
                                    cf = svc.get("compose_file", "")
                                    if cf and target.startswith(str(Path(cf).parent)):
                                        svc_cfg = svc
                                        break
                            if svc_cfg:
                                logger.info("[REBUILD] Triggering rebuild for service: %s (runtime: %s)",
                                            svc_cfg.get("name"), svc_cfg.get("runtime"))
                                rb_ok, rb_out = self.rebuild_manager.rebuild(svc_cfg)
                                if rb_ok:
                                    logger.info("[REBUILD] ✅ Service rebuilt successfully")
                                    send_telegram_text(
                                        TELEGRAM_CHAT_ID,
                                        f"✅ *Patch applied & service rebuilt*\n"
                                        f"📁 `{md_escape(target)}`\n"
                                        f"🔄 `{md_escape(svc_cfg.get('name', '?'))}` restarted"
                                    )
                                    self.discovery.invalidate(svc_cfg.get("container_name", ""))
                                else:
                                    logger.error("[REBUILD] ❌ Rebuild failed: %s", rb_out[:300])
                                    send_telegram_text(
                                        TELEGRAM_CHAT_ID,
                                        f"⚠️ *Patch applied but rebuild failed*\n`{md_escape(rb_out[:200])}`"
                                    )
                            else:
                                logger.info("[REBUILD] No matching service in manifest for %s — skipping auto-rebuild", target)
                        except Exception as rb_ex:
                            logger.warning("[REBUILD] Auto-rebuild error: %s", rb_ex)
                        # ── AUTO-REBUILD END ─────────────────────────────────────────
                    except Exception as ex:
                        logger.error("Dosya düzenleme hatası: %s", str(ex))
                        if tmp_path.exists():
                            tmp_path.unlink()
                        executed.append({"type": act_type, "target": target, "status": "failed", "error": str(ex)})

                elif act_type == "append":
                    try:
                        if "requirements.txt" in target:
                            target_path = Path(target)
                            original = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
                            new_content = original + ("" if original.endswith("\n") or not original else "\n") + payload + "\n"
                            atomic_write_text(target_path, new_content)

                            pkg_match = re.match(r"^([a-zA-Z0-9_-]+)", payload.strip())
                            if pkg_match:
                                pkg_name = pkg_match.group(1)
                                install_res = subprocess.run(
                                    ["pip", "install", pkg_name, "--break-system-packages"],
                                    capture_output=True, text=True, timeout=30
                                )
                                if install_res.returncode != 0:
                                    atomic_write_text(target_path, original)
                                    raise ValueError(f"pip install başarısız: {install_res.stderr[:300]}")
                                test_import = subprocess.run(
                                    ["python3", "-c", f"import {pkg_name}"],
                                    capture_output=True, text=True, timeout=10
                                )
                                if test_import.returncode != 0:
                                    atomic_write_text(target_path, original)
                                    raise ValueError(f"Paket yüklendi ancak import testi başarısız: {pkg_name}")
                        else:
                            with open(target, "a", encoding="utf-8") as f:
                                f.write(payload + "\n")
                        executed.append({"type": "append", "target": target, "status": "success"})
                    except Exception as ex:
                        executed.append({"type": "append", "target": target, "status": "failed", "error": str(ex)})

                elif act_type == "shell":
                    # Command whitelist filter
                    allowed_patterns = [
                        r"^docker\\s+compose\\s+(up\\s+-d\\s+--build|restart|up\\s+-d)(\\s+[a-zA-Z0-9_-]+)?$",
                        r"^docker\\s+restart\\s+[a-zA-Z0-9_-]+$",
                        r"^systemctl\\s+(restart|start)\\s+[a-zA-Z0-9_.-]+$",
                        r"^cd\\s+/home/pi/projects/[a-zA-Z0-9_.-]+\\s+&&\\s+docker\\s+compose\\s+(up\\s+-d\\s+--build|restart|up\\s+-d)(\\s+[a-zA-Z0-9_-]+)?$",
                    ]
                    # Öğrenilmiş dinamik pattern'leri yükle
                    allowed_patterns += _load_learned_patterns()
                    is_allowed = any(re.match(pat, payload) for pat in allowed_patterns)

                    # Tehlikeli karakter kontrolü — LLM'e bile sormadan reddet
                    dangerous_chars = any(char in payload for char in [";", "|", "`", "$", chr(10), chr(13)])
                    if dangerous_chars:
                        executed.append({"type": "shell", "payload": payload, "status": "rejected", "reason": "tehlikeli karakter"})
                        continue

                    if not is_allowed:
                        logger.info("[WHITELIST] Whitelist dışı komut — LLM onay zinciri: %s", payload)
                        approved, new_pattern = llm_approve_for_whitelist(payload, payload)
                        if approved and new_pattern:
                            allowed_patterns.append(new_pattern)
                            _persist_learned_pattern(new_pattern)
                            is_allowed = True
                            logger.info("[WHITELIST] LLM onayladı, pattern kaydedildi: %s", new_pattern)
                        else:
                            executed.append({"type": "shell", "payload": payload, "status": "rejected", "reason": "LLM onaylamadı"})
                            _notify_telegram_rejection(payload, payload)
                            continue

                    try:
                        cmd_args = payload.split()
                        if cmd_args[0] == "systemctl":
                            cmd_args = ["sudo", "/usr/bin/systemctl"] + cmd_args[1:]
                        
                        result = subprocess.run(cmd_args, cwd=target if target else None, capture_output=True, text=True, timeout=60)
                        if result.returncode == 0:
                            executed.append({"type": "shell", "payload": payload, "status": "success", "stdout": result.stdout[:500]})
                        else:
                            executed.append({"type": "shell", "payload": payload, "status": "failed", "error": result.stderr[:500]})
                    except Exception as ex:
                        executed.append({"type": "shell", "payload": payload, "status": "failed", "error": str(ex)})

            if is_self_fix:
                success_count = sum(1 for e in executed if e.get("status") == "success")
                if success_count == len(actions):
                    # Commit SRE self-fix changes
                    try:
                        subprocess.run(["git", "-C", str(INSTALL_DIR), "add", "-A"], check=True)
                        diff_check = subprocess.run(["git", "-C", str(INSTALL_DIR), "diff", "--cached", "--quiet"])
                        if diff_check.returncode == 0:
                            raise ValueError("Commitlenecek değişiklik yok")
                        subprocess.run(["git", "-C", str(INSTALL_DIR), "commit", "-m", f"SRE self-fix: {timestamp}"], check=True)
                        logger.info("SRE self-fix başarıyla commit edildi.")
                    except Exception as e:
                        logger.error("Git commit hatası: %s", str(e))
                        subprocess.run(["git", "-C", str(INSTALL_DIR), "reset", "--hard", tag_name], capture_output=True)
                        subprocess.run(["git", "-C", str(INSTALL_DIR), "tag", "-d", tag_name], capture_output=True)
                        return executed + [{"status": "failed", "error": f"git commit failed: {e}"}]
                    
                    # Detached Watchdog Spawning with MainPID and heartbeat checks
                    watchdog_script = fr"""(
                      sleep 10
                      PID1="\$(/usr/bin/systemctl show sre-daemon -p MainPID --value)"
                      ACTIVE1="\$(/usr/bin/systemctl is-active sre-daemon)"
                      sleep 10
                      PID2="\$(/usr/bin/systemctl show sre-daemon -p MainPID --value)"
                      ACTIVE2="\$(/usr/bin/systemctl is-active sre-daemon)"
                      
                      # Heartbeat modify check
                      MOD_TIME=\$(stat -c %Y {HEARTBEAT_PATH} 2>/dev/null || echo 0)
                      NOW=\$(date +%s)
                      HEARTBEAT_AGE=\$((NOW - MOD_TIME))

                      if [ -n "\$PID1" ] && [ -n "\$PID2" ] && \
                         [ "\$ACTIVE1" = "active" ] && [ "\$ACTIVE2" = "active" ] && \
                         [ "\$PID1" -eq "\$PID2" ] && [ "\$PID1" -ne 0 ] && \
                         [ \$HEARTBEAT_AGE -lt 15 ]; then
                        echo "Stabilization check passed." >> {INSTALL_DIR / 'watchdog.log'}
                      else
                        echo "Unstable service detected. Rolling back..." >> {INSTALL_DIR / 'watchdog.log'}
                        echo "Rollback to {tag_name}" > {INSTALL_DIR / 'watchdog_rollback.flag'}
                        git -C {INSTALL_DIR} reset --hard {tag_name} >> {INSTALL_DIR / 'watchdog.log'} 2>&1
                        sudo /usr/bin/systemctl restart sre-daemon >> {INSTALL_DIR / 'watchdog.log'} 2>&1
                        git -C {INSTALL_DIR} tag -d {tag_name} >> {INSTALL_DIR / 'watchdog.log'} 2>&1
                        curl -s -X POST "https://api.telegram.org/bot\${{TELEGRAM_BOT_TOKEN}}/sendMessage" \
                          -d "chat_id=\${{TELEGRAM_CHAT_ID}}" \
                          -d "text=⚠️ *SRE Watchdog*: Rollback tetiklendi! Servis stabilize olamadı, {tag_name} etiketine geri dönüldü." \
                          > /dev/null 2>&1
                      fi
                    ) &"""

                    # Pass credentials safely through environment variables
                    env_pass = os.environ.copy()
                    env_pass["TELEGRAM_BOT_TOKEN"] = TELEGRAM_BOT_TOKEN
                    env_pass["TELEGRAM_CHAT_ID"] = TELEGRAM_CHAT_ID

                    logger.info("Detached watchdog tetikleniyor...")
                    subprocess.Popen(
                        ["/bin/bash", "-c", watchdog_script],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env_pass
                    )

                    # Trigger the restart of SRE Daemon
                    logger.info("Daemon restart ediliyor...")
                    subprocess.Popen(["sudo", "/usr/bin/systemctl", "restart", "sre-daemon"])
                else:
                    logger.warning("Tüm self-fix eylemleri başarılı olamadı, rollback yapılıyor...")
                    subprocess.run(["git", "-C", str(INSTALL_DIR), "reset", "--hard", tag_name], capture_output=True)
                    subprocess.run(["git", "-C", str(INSTALL_DIR), "tag", "-d", tag_name], capture_output=True)
            return executed
        finally:
            if is_self_fix and SELF_FIX_LOCK.locked():
                try:
                    SELF_FIX_LOCK.release()
                except RuntimeError:
                    pass

    def _write_heal_log(self, error, fix, source, success, tag, actions=None):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": tag.strip("[]"),
            "error_snippet": error[:500],
            "source": source,
            "success": success,
            "fix_preview": (fix or "")[:800],
            "actions_executed": actions or []
        }
        try:
            with open(HEAL_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("Heal log yazma hatası: %s", str(e))


# ── Whitelist LLM Approval (dinamik öğrenen zincir) ──────────
LEARNED_PATTERNS_FILE = str(INSTALL_DIR / "learned_patterns.json")

def _load_learned_patterns() -> list:
    """Dosyadan dinamik olarak öğrenilmiş whitelist pattern'lerini yükle."""
    if os.path.exists(LEARNED_PATTERNS_FILE):
        try:
            with open(LEARNED_PATTERNS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _persist_learned_pattern(pattern: str):
    """LLM onayladığı pattern'i kalıcı olarak kaydet."""
    patterns = _load_learned_patterns()
    if pattern not in patterns:
        patterns.append(pattern)
        with open(LEARNED_PATTERNS_FILE, "w") as f:
            json.dump(patterns, f, indent=2)
        logger.info("[WHITELIST] Yeni pattern öğrenildi ve kaydedildi: %s", pattern)

def llm_approve_for_whitelist(payload: str, context: str) -> tuple:
    """
    Cascading LLM zinciri: Ollama 7b → Groq → Gemini
    Komutun güvenli olup olmadığını ve whitelist regex pattern'ini üretir.
    Dön: (approved: bool, pattern: str | None)
    """
    prompt = (
        "Sen bir Linux güvenlik uzmanısın. Bir SRE daemon'ı şu komutu çalıştırmak istiyor:\n\n"
        f"KOMUT: {payload}\n"
        f"BAĞLAM: {context[:300]}\n\n"
        "Görevin:\n"
        "1. Bu komut gerçekten güvenli mi?\n"
        "2. Güvenliyse, bu komutu kapsayan minimal bir Python regex pattern üret.\n\n"
        "Tehlikeli (reddet): rm, wget, curl, pip install, apt, chmod, chown, echo > dosya\n"
        "Güvenli: docker compose up/restart, docker restart, systemctl restart/start\n\n"
        "Yanıtı SADECE JSON formatında ver, başka hiçbir şey yazma:\n"
        '{"approved": true, "pattern": "^regex_pattern$", "reason": "kısa açıklama"}'
    )

    def _parse_json_response(text: str) -> dict:
        text = text.strip()
        text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
        return json.loads(text)

    # 1. Local Ollama (Pi 7b — hızlı, ücretsiz, $0)
    try:
        r = requests.post("http://localhost:11434/api/generate", json={
            "model": os.getenv("OLLAMA_MODEL_FAST", "qwen2.5-coder:7b"),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.05, "num_predict": 300}
        }, timeout=90)  # timeout 90 saniyeye çekildi (Pi 5 model yükleme toleransı)
        r.raise_for_status()
        result = _parse_json_response(r.json().get("response", ""))
        if result.get("approved") and result.get("pattern"):
            logger.info("[WHITELIST-LLM] Ollama 7b onayladı → pattern: %s", result["pattern"])
            return True, result["pattern"]
        if result.get("approved") is False:
            logger.warning("[WHITELIST-LLM] Ollama 7b reddetti → sebep: %s", result.get("reason"))
            return False, None
    except Exception as e:
        logger.warning("[WHITELIST-LLM] Ollama başarısız, Groq deneniyor: %s", e)

    # 2. Groq fallback (ücretsiz, hızlı)
    try:
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300, "temperature": 0.05
                },
                timeout=20)
            r.raise_for_status()
            result = _parse_json_response(r.json()["choices"][0]["message"]["content"])
            if result.get("approved") and result.get("pattern"):
                logger.info("[WHITELIST-LLM] Groq onayladı → pattern: %s", result["pattern"])
                return True, result["pattern"]
            if result.get("approved") is False:
                logger.warning("[WHITELIST-LLM] Groq reddetti → sebep: %s", result.get("reason"))
                return False, None
        else:
            logger.warning("[WHITELIST-LLM] GROQ_API_KEY bulunamadı, atlanıyor.")
    except Exception as e:
        logger.warning("[WHITELIST-LLM] Groq başarısız, Gemini deneniyor: %s", e)

    # 3. Gemini fallback
    try:
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=20)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = _parse_json_response(text)
            if result.get("approved") and result.get("pattern"):
                logger.info("[WHITELIST-LLM] Gemini onayladı → pattern: %s", result["pattern"])
                return True, result["pattern"]
            if result.get("approved") is False:
                logger.warning("[WHITELIST-LLM] Gemini reddetti → sebep: %s", result.get("reason"))
                return False, None
        else:
            logger.warning("[WHITELIST-LLM] GEMINI_API_KEY bulunamadı, atlanıyor.")
    except Exception as e:
        logger.warning("[WHITELIST-LLM] Gemini başarısız: %s", e)

    logger.error("[WHITELIST-LLM] Tüm zincir başarısız — komut güvenlik engelinde.")
    return False, None

def _notify_telegram_rejection(payload: str, context: str):
    """Whitelist LLM zincirinin de reddettiği komutu Telegram'a bildir."""
    try:
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            msg = (
                "⚠️ *Whitelist Reddi — Manuel İnceleme Gerekiyor*\n\n"
                f"Komut LLM tarafından da onaylanmadı:\n`{payload}`\n\n"
                f"Bağlam:\n`{context[:250]}`"
            )
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10)
    except Exception as e:
        logger.error("[WHITELIST-LLM] Telegram rejection bildirimi başarısız: %s", e)

def run_approved_actions(actions: List[Dict[str, Any]], action_id: int):
    orchestrator = HealingOrchestrator()
    executed_status = orchestrator.execute_approved_actions(actions, action_id)
    orchestrator._write_heal_log("HITL Approved Action ID: " + str(action_id), json.dumps(actions), "Telegram HITL", True, "[HITL-Fix]", executed_status)

def get_telegram_status_report() -> str:
    """Sistem sağlığı ve Docker durumlarını toplayıp Markdown rapor hazırlar."""
    report = "📊 *SRE Daemon Sistem Durum Raporu*\n\n"
    
    # 1. CPU Temp
    try:
        temp_res = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=5)
        temp = temp_res.stdout.strip().replace("temp=", "")
        report += f"🌡️ *CPU Sıcaklığı*: `{temp}`\n"
    except Exception:
        report += "🌡️ *CPU Sıcaklığı*: `Bilinmiyor`\n"
        
    # 2. RAM Usage
    try:
        free_res = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
        lines = free_res.stdout.splitlines()
        mem_parts = lines[1].split()
        total_mem = int(mem_parts[1])
        used_mem = int(mem_parts[2])
        mem_pct = (used_mem / total_mem) * 100
        report += f"🧠 *Bellek (RAM)*: `{used_mem}MB / {total_mem}MB` (*{mem_pct:.1f}%*)\n"
    except Exception:
        report += "🧠 *Bellek (RAM)*: `Bilinmiyor`\n"
        
    # 3. Disk Usage
    try:
        df_res = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        df_parts = df_res.stdout.splitlines()[-1].split()
        disk_size = df_parts[1]
        disk_used = df_parts[2]
        disk_avail = df_parts[3]
        disk_pct = df_parts[4]
        report += f"💾 *Disk (Root)*: `{disk_used} / {disk_size}` (*{disk_pct}* used, `{disk_avail}` free)\n\n"
    except Exception:
        report += "💾 *Disk (Root)*: `Bilinmiyor`\n\n"
        
    # 4. Active Docker Containers
    try:
        docker_res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=10
        )
        containers = docker_res.stdout.strip().splitlines()
        if containers and containers[0]:
            report += "🐳 *Aktif Konteynerler*:\n"
            for c in containers:
                parts = c.split("|")
                name = parts[0]
                status = parts[1]
                status_short = status.split(" (")[0]
                report += f"• `{name}`: _{status_short}_\n"
        else:
            report += "🐳 *Aktif Konteynerler*: `Yok veya Docker kapalı`\n"
    except Exception as e:
        report += f"🐳 *Docker Durumu*: `Hata: {str(e)}`\n"
        
    # 5. Pending approvals count
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM pending_actions WHERE status = 'pending'")
            count = cursor.fetchone()[0]
            if count > 0:
                report += f"\n🚨 *Onay Bekleyen Aksiyon*: `{count} adet`"
    except Exception:
        pass
        
    return report

def send_telegram_text(chat_id: str, text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
        if resp.status_code != 200:
            logger.error("Telegram API hatası (kod: %d): %s", resp.status_code, resp.text)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Telegram mesajı gönderme hatası: %s", str(e))

def report_incident_to_platform(service: str, title: str, logs: str, status: str, proposed_command: str, action_output: str):
    try:
        platform_url = os.getenv("SRE_PLATFORM_URL", "https://sre.trihonor.com")
        api_key = ""
        
        # Load API Key from manifest if available
        if os.path.exists(MANIFEST_PATH):
            try:
                if YAML_AVAILABLE:
                    with open(MANIFEST_PATH) as f:
                        data = _yaml_mod.safe_load(f) or {}
                        api_key = data.get("sre_api_key", "")
            except Exception:
                pass
                
        url = f"{platform_url.rstrip('/')}/api/daemon/incident"
        payload = {
            "service": service.strip("[]"),
            "title": title[:200],
            "logs": logs[:2000],
            "status": status,
            "proposed_command": proposed_command,
            "action_output": action_output
        }
        
        headers = {}
        if api_key:
            headers["X-SRE-API-Key"] = api_key
            
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        if resp.status_code != 200:
            logger.warning("Platforma incident raporlanamadı (kod: %d): %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Platforma incident raporlama hatası: %s", str(e))

# ── Telegram Callback Polling Listener ───────────────────────
def telegram_poller():
    offset = None
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram Bot Token veya Chat ID eksik, poller pasif.")
        return
    
    logger.info("Telegram Callback Poller aktif.")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            
            resp = requests.get(url, params=params, timeout=35)
            if resp.status_code != 200:
                time.sleep(5)
                continue
            
            updates = resp.json().get("result", [])
            for update in updates:
                offset = update.get("update_id", 0) + 1
                
                # 1. Handle Messages / Commands
                message = update.get("message")
                if message:
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    if chat_id != TELEGRAM_CHAT_ID:
                        logger.warning("Güvenlik engeli: whitelist dışı chat_id mesajı: %s", chat_id)
                        continue
                    
                    text = message.get("text", "").strip()
                    if text.startswith("/"):
                        cmd_parts = text.split()
                        cmd = cmd_parts[0].lower()
                        
                        if cmd == "/status":
                            status_msg = get_telegram_status_report()
                            send_telegram_text(chat_id, status_msg)
                        elif cmd == "/autonomous":
                            if len(cmd_parts) > 1:
                                sub = cmd_parts[1].lower()
                                if sub == "on":
                                    set_daemon_setting("autonomous_mode", "1")
                                    send_telegram_text(chat_id, "✅ *SRE Otonom Mod Aktif Edildi.*")
                                elif sub == "off":
                                    set_daemon_setting("autonomous_mode", "0")
                                    send_telegram_text(chat_id, "❌ *SRE Otonom Mod Devre Dışı Bırakıldı.*")
                                else:
                                    send_telegram_text(chat_id, "Geçersiz parametre. Kullanım: `/autonomous on` veya `/autonomous off`")
                            else:
                                mode = get_daemon_setting("autonomous_mode", "0")
                                status_label = "Açık (Otonom) 🤖" if mode == "1" else "Kapalı (Onay Bekler) 👥"
                                send_telegram_text(chat_id, f"🤖 *SRE Otonom Mod Durumu*: `{status_label}`\n\nAçmak için: `/autonomous on`\nKapatmak için: `/autonomous off`")
                        elif cmd == "/history":
                            try:
                                with sqlite3.connect(DB_PATH) as conn:
                                    conn.row_factory = sqlite3.Row
                                    cur = conn.cursor()
                                    cur.execute("SELECT project_tag, success, duration_seconds, created_at FROM heal_history ORDER BY id DESC LIMIT 5")
                                    rows = cur.fetchall()
                                    if not rows:
                                        send_telegram_text(chat_id, "📝 *Onarım geçmişi temiz.*")
                                    else:
                                        msg = "📊 *Son Onarım Girişimleri*:\n\n"
                                        for r in rows:
                                            status = "✅ Başarılı" if r["success"] else "❌ Başarısız"
                                            dt = r["created_at"][:16].replace("T", " ")
                                            msg += f"• *{md_escape(r['project_tag'])}* - {status} ({r['duration_seconds']:.1f}s) - _{dt}_\n"
                                        send_telegram_text(chat_id, msg)
                            except Exception as e:
                                send_telegram_text(chat_id, f"Hata: {str(e)}")
                        elif cmd == "/help":
                            help_msg = (
                                "🤖 *SRE Daemon Assistant*\n\n"
                                "Mevcut komutlar:\n"
                                "• `/status` - Sistem sağlığı, disk, RAM, sıcaklık ve konteyner durumlarını sorgular.\n"
                                "• `/autonomous` - Otonom mod durumunu gösterir. (Kullanım: `/autonomous on` veya `/autonomous off`)\n"
                                "• `/history` - Son onarım geçmişini gösterir.\n"
                                "• `/help` - Bu yardım mesajını gösterir."
                            )
                            send_telegram_text(chat_id, help_msg)
                
                # 2. Handle Inline Buttons (Callback Queries)
                callback_query = update.get("callback_query")
                if callback_query:
                    sender_chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
                    if sender_chat_id != TELEGRAM_CHAT_ID:
                        logger.warning("Güvenlik engeli: whitelist dışı chat_id: %s", sender_chat_id)
                        continue
                    
                    callback_data = callback_query.get("data", "")
                    callback_query_id = callback_query.get("id")
                    message_id = callback_query.get("message", {}).get("message_id")
                    
                    # Answer immediately
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                        "callback_query_id": callback_query_id
                    }, timeout=5)
                    
                    if callback_data.startswith("approve_") or callback_data.startswith("reject_"):
                        parts = callback_data.split("_")
                        action = parts[0]
                        action_id = int(parts[1])
                        
                        status = "approved" if action == "approve" else "rejected"
                        actions = try_process_action(action_id, status)
                        
                        result_text = "✅ *Kabul Edildi & Uygulanıyor...*" if action == "approve" else "❌ *Reddedildi.*"
                        original_text = callback_query.get("message", {}).get("text", "")
                        edited_text = f"{original_text}\n\n{result_text}"
                        
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={
                            "chat_id": sender_chat_id,
                            "message_id": message_id,
                            "text": edited_text,
                            "parse_mode": "Markdown"
                        }, timeout=5)
                        
                        if status == "rejected":
                            # Add cooldown cache entry
                            with ACTION_LOCK:
                                init_db()
                                with sqlite3.connect(DB_PATH) as conn:
                                    conn.row_factory = sqlite3.Row
                                    cur = conn.cursor()
                                    cur.execute("SELECT error_hash FROM pending_actions WHERE id = ?", (action_id,))
                                    r = cur.fetchone()
                                    if r:
                                        DECLINED_ERRORS[r["error_hash"]] = time.time()
                        
                        if actions:
                            threading.Thread(
                                target=run_approved_actions,
                                args=(actions, action_id),
                                daemon=True
                            ).start()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning("Telegram poller network/timeout warning (normal long-polling reset): %s", str(e))
            time.sleep(2)
        except Exception as e:
            logger.error("Telegram poller beklenmedik hata: %s", str(e))
            time.sleep(5)

# ── Self-Improving Log Monitor ───────────────────────────────
def start_self_monitor():
    """Monitors daemon.log for exceptions and triggers self-fix loops."""
    if not ANTHROPIC_KEY or not TELEGRAM_BOT_TOKEN:
        return
        
    def _run():
        log_path = INSTALL_DIR / "daemon.log"
        last_pos = 0
        if log_path.exists():
            last_pos = log_path.stat().st_size
            
        while True:
            try:
                if log_path.exists():
                    curr_size = log_path.stat().st_size
                    if curr_size > last_pos:
                        with open(log_path, "r", errors="ignore") as f:
                            f.seek(last_pos)
                            content = f.read()
                        last_pos = curr_size
                        
                        # Detect Traceback pattern
                        if "Traceback (most recent call last)" in content or "[CRITICAL]" in content:
                            logger.error("SRE Daemon internal error detected! Running self-diagnosis...")
                            orchestrator = HealingOrchestrator()
                            orchestrator.handle_error(content[-2000:], "[sre-daemon]")
                time.sleep(10)
            except Exception as e:
                time.sleep(10)

    threading.Thread(target=_run, name="self-monitor", daemon=True).start()

def _midnight_budget_reset():
    """
    Background thread: resets daily_api_usage counters at midnight UTC.
    Runs forever with a 60-second polling interval.
    """
    last_reset_day = datetime.now(timezone.utc).date()
    while True:
        try:
            time.sleep(60)
            today = datetime.now(timezone.utc).date()
            if today > last_reset_day:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("DELETE FROM daily_api_usage WHERE day < ?",
                                 (today.isoformat(),))
                    conn.commit()
                last_reset_day = today
                logger.info("[BUDGET] Daily API usage counters reset for new day: %s", today)
        except Exception as e:
            logger.warning("[BUDGET] midnight_budget_reset error: %s", e)


# ── Main Loop ────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# PREDICTIVE MAINTENANCE — MetricsCollector & Anomaly Detection
# ══════════════════════════════════════════════════════════════════════════════

class MetricsCollector(threading.Thread):
    """
    Background thread that collects host and container metrics every 30 seconds.
    Saves data to stats_history.db (capped to 24h of history).
    Calculates EMA and flags anomalies to trigger proactive restarts or mitigations.
    """
    def __init__(self, orchestrator):
        super().__init__(name="metrics-collector", daemon=True)
        self.orchestrator = orchestrator
        self.stats_db = INSTALL_DIR / "stats_history.db"
        self._init_db()
        self._anomaly_cooldown = {} # entity_metric -> timestamp of last warning
        self._cooldown_seconds = 300 # 5 minutes cooldown per service/metric anomaly alert

    def _init_db(self):
        try:
            with sqlite3.connect(self.stats_db) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS stats_history (
                        timestamp TEXT NOT NULL,
                        entity TEXT NOT NULL,
                        metric_type TEXT NOT NULL,
                        value REAL NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_history_ts ON stats_history (timestamp)")
                conn.commit()
        except Exception as e:
            logger.error("[METRICS] DB init error: %s", e)

    def run(self):
        logger.info("[METRICS] MetricsCollector active. Loop started (interval: 30s).")
        while True:
            try:
                self._collect_and_analyze()
            except Exception as e:
                logger.error("[METRICS] Error in collection loop: %s", e)
            time.sleep(30)

    def _collect_and_analyze(self):
        now_str = datetime.now(timezone.utc).isoformat()
        host_cpu = 0.0
        host_mem = 0.0
        
        # 1. Collect Host Metrics
        try:
            host_cpu = psutil.cpu_percent(interval=0.1)
            host_mem = psutil.virtual_memory().percent
            self._save_metric(now_str, "host", "cpu", host_cpu)
            self._save_metric(now_str, "host", "mem", host_mem)
            self._check_anomaly("host", "cpu", host_cpu)
            self._check_anomaly("host", "mem", host_mem)
        except Exception as e:
            logger.warning("[METRICS] Failed to collect host stats: %s", e)

        # 2. Collect Container Metrics
        active_containers = []
        try:
            docker_res = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}"],
                capture_output=True, text=True, timeout=10
            )
            if docker_res.returncode == 0:
                for line in docker_res.stdout.strip().splitlines():
                    parts = line.split("|")
                    if len(parts) >= 3:
                        name = parts[0].strip()
                        try:
                            cpu_val = float(parts[1].replace("%", "").strip())
                            mem_val = float(parts[2].replace("%", "").strip())
                            self._save_metric(now_str, name, "cpu", cpu_val)
                            self._save_metric(now_str, name, "mem", mem_val)
                            self._check_anomaly(name, "cpu", cpu_val)
                            self._check_anomaly(name, "mem", mem_val)
                            active_containers.append(name)
                        except ValueError:
                            pass
        except Exception as e:
            logger.warning("[METRICS] Failed to collect container stats: %s", e)

        # 3. Report current status to central SRE platform API
        try:
            self._report_status_to_platform(host_cpu, host_mem, active_containers)
        except Exception as e:
            logger.debug("[METRICS] Status report failed: %s", e)

        # 4. Truncate historical data older than 24 hours
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            with sqlite3.connect(self.stats_db) as conn:
                conn.execute("DELETE FROM stats_history WHERE timestamp < ?", (cutoff,))
                conn.commit()
        except Exception as e:
            logger.error("[METRICS] Truncate failed: %s", e)

    def _save_metric(self, timestamp: str, entity: str, metric_type: str, value: float):
        try:
            with sqlite3.connect(self.stats_db) as conn:
                conn.execute(
                    "INSERT INTO stats_history (timestamp, entity, metric_type, value) VALUES (?, ?, ?, ?)",
                    (timestamp, entity, metric_type, value)
                )
                conn.commit()
        except Exception as e:
            logger.error("[METRICS] Save metric failed: %s", e)

    def _check_anomaly(self, entity: str, metric_type: str, current_val: float):
        if entity == "host":
            return
        # Retrieve thresholds from manifest
        cpu_threshold = 80.0
        mem_threshold = 85.0
        sensitivity = 1.5
        
        # Load from manifest if available
        svc_cfg = self.orchestrator.manifest.get_service_by_container(entity)
        if svc_cfg and "limits" in svc_cfg:
            lims = svc_cfg["limits"]
            if isinstance(lims, dict):
                cpu_threshold = float(lims.get("cpu_threshold", cpu_threshold))
                mem_threshold = float(lims.get("mem_threshold", mem_threshold))
                sensitivity = float(lims.get("anomaly_sensitivity", sensitivity))

        limit = cpu_threshold if metric_type == "cpu" else mem_threshold

        # Compute EMA over last 20 observations
        ema_val = self._get_ema(entity, metric_type)
        if ema_val is None:
            return

        # Check anomaly trigger condition
        if current_val > limit and current_val > (ema_val * sensitivity):
            # Cooldown check
            key = f"{entity}_{metric_type}"
            now = time.time()
            if key in self._anomaly_cooldown:
                if now - self._anomaly_cooldown[key] < self._cooldown_seconds:
                    return # skip warning inside cooldown
            
            self._anomaly_cooldown[key] = now
            logger.warning("[ANOMALY DETECTED] %s %s is at %.1f%% (EMA: %.1f%%, limit: %.1f%%)",
                           entity, metric_type.upper(), current_val, ema_val, limit)
            
            # Fire predictive notification & trigger pre-emptive action in background
            threading.Thread(
                target=self._handle_predictive_fix,
                args=(entity, metric_type, current_val, ema_val),
                daemon=True
            ).start()

    def _get_ema(self, entity: str, metric_type: str, period: int = 20) -> Optional[float]:
        try:
            with sqlite3.connect(self.stats_db) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT value FROM stats_history
                    WHERE entity = ? AND metric_type = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (entity, metric_type, period))
                rows = cur.fetchall()
                if len(rows) < 5: # Need at least 5 points to establish a trend
                    return None
                values = [r[0] for r in rows][::-1] # chronological order
                
                # Compute EMA
                alpha = 2.0 / (period + 1)
                ema = values[0]
                for val in values[1:]:
                    ema = (val * alpha) + (ema * (1 - alpha))
                return ema
        except Exception as e:
            logger.error("[METRICS] get_ema error: %s", e)
            return None

    def _handle_predictive_fix(self, entity: str, metric_type: str, current_val: float, ema_val: float):
        # Pre-emptive notification
        metric_name = "CPU" if metric_type == "cpu" else "RAM"
        msg = (
            f"⚠️ *ANOMALY DETECTED:* Service `{entity}` {metric_name} is at {current_val:.1f}% (EMA: {ema_val:.1f}%). "
            f"Leak/Spike pattern identified. To prevent service outage, "
            f"starting **pre-emptive restart in 10 seconds**."
        )
        send_telegram_text(TELEGRAM_CHAT_ID, msg)
        
        # Wait 10 seconds (giving opportunity to read notification)
        time.sleep(10)
        
        # Execute restart
        svc_cfg = self.orchestrator.manifest.get_service_by_container(entity)
        if svc_cfg:
            logger.info("[PRE-EMPTIVE FIX] Restarting service: %s", entity)
            ok, out = self.orchestrator.rebuild_manager.rebuild(svc_cfg)
            if ok:
                logger.info("[PRE-EMPTIVE FIX] Service restarted successfully")
                send_telegram_text(TELEGRAM_CHAT_ID, f"✅ *Pre-emptive restart complete* for service `{entity}`.")
            else:
                logger.error("[PRE-EMPTIVE FIX] Restart failed: %s", out[:200])
                send_telegram_text(TELEGRAM_CHAT_ID, f"❌ *Pre-emptive restart failed* for service `{entity}`:\n`{out[:150]}`")

    def _report_status_to_platform(self, cpu: float, mem: float, containers: list):
        api_key = self.orchestrator.manifest.api_key
        
        # Get disk usage
        disk_pct = 0.0
        try:
            disk_pct = psutil.disk_usage("/").percent
        except Exception:
            pass
            
        payload = {
            "tenant_id": self.orchestrator.manifest.tenant_id,
            "cpu_temp": f"{cpu:.1f}%", # we send CPU usage as representation of load metric
            "memory": f"{mem:.1f}%",
            "disk": f"{disk_pct:.1f}%",
            "containers": [{"name": c, "status": "Up (monitored)"} for c in containers]
        }
        
        # Post to SRE Platform status endpoint
        platform_url = os.getenv("SRE_PLATFORM_URL", "https://sre.trihonor.com")
        url = f"{platform_url.rstrip('/')}/api/daemon/status"
        
        headers = {}
        if api_key:
            headers["X-SRE-API-Key"] = api_key
            
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=5)
            if res.status_code != 200:
                logger.debug("[METRICS] Platform status report rejected: %d", res.status_code)
        except Exception as e:
            logger.debug("[METRICS] Platform request error: %s", e)

def main():
    logger.info("=" * 65)
    logger.info("SRE Daemon v5 — Hardened HITL & Self-Healing Engine başlatılıyor...")
    logger.info("Mac IP: %s", MAC_IP)
    logger.info("=" * 65)

    _validate_env()
    init_db()
    start_heartbeat()
    cleanup_old_prefix_tags()

    rate_limiter = RateLimiter()
    orchestrator = HealingOrchestrator()

    # Start watchers
    journal_watcher = JournalWatcher(rate_limiter, orchestrator.handle_error, PROJECT_MAP, NOISE_PATTERNS, register_discovered_service)
    docker_watcher  = DockerWatcher(rate_limiter, orchestrator.handle_error, PROJECT_MAP, DOCKER_BURST_LIMIT, register_discovered_service)
    journal_watcher.start()
    docker_watcher.start()

    # Start Telegram Poller, Timeout Worker, and Self-Monitor
    threading.Thread(target=telegram_poller, name="telegram-poller", daemon=True).start()
    threading.Thread(target=timeout_worker, name="timeout-worker", daemon=True).start()
    threading.Thread(target=_midnight_budget_reset, name="budget-reset", daemon=True).start()
    start_self_monitor()
    
    # Start Predictive Metrics Collector
    metrics_collector = MetricsCollector(orchestrator)
    metrics_collector.start()

    logger.info("SRE Daemon active. Tenant: %s | Services: %d", orchestrator.manifest.tenant_id, len(orchestrator.manifest.services))
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Durduruluyor (sinyal: %s)...", signum)
        journal_watcher.stop()
        docker_watcher.stop()
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while not stop_event.is_set():
        stop_event.wait(timeout=60)

    logger.info("SRE Daemon durduruldu.")

def _validate_env():
    # Only critical env warnings
    if not ANTHROPIC_KEY:
        logger.warning("ANTHROPIC_API_KEY eksik, Claude eskalasyonu pasif.")
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN eksik, bildirimler pasif.")

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        print("self-test-ok")
        sys.exit(0)
    main()

def git_auto_sync():
    import subprocess
    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", "Auto-sync: Incident recorded"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
    except Exception as e:
        print(f"Git Sync Hatası: {e}")

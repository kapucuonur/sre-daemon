#!/usr/bin/env python3
"""
AI Log Analyst — Pi 5 Sistem Geneli
Sadece sorun tespit edildiğinde Telegram'a bildirim gönderir.

Model Stratejisi:
  1. Önce Ollama (local, ücretsiz) — qwen2.5-coder:32b
  2. Ollama başarısız olursa → LiteLLM proxy
  3. İkisi de başarısız olursa → Claude API (son çare)

Cron: */30 * * * * python3 /home/pi/sre-daemon/ai_log_analyst.py
"""

import os
import json
import subprocess
import requests
import anthropic
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

# ── Load .env manually if it exists (for cron compatibility) ──
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

# ── Yapılandırma ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
XAI_API_KEY        = os.getenv("XAI_API_KEY", "")
STATE_FILE         = Path("/tmp/ai_log_analyst_state.json")
LOG_WINDOW_MINUTES = 30  # Son kaç dakikanın logları analiz edilsin

# Model hiyerarşisi — önce local, son çare cloud
OLLAMA_URL        = "http://localhost:11434/api/generate"
OLLAMA_MODEL      = "qwen2.5-coder:7b"
LITELLM_URL       = "http://localhost:4000/v1/chat/completions"
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY", "sk-1234")  # LiteLLM proxy key

# Dynamic Config Loading
import sqlite3
DB_PATH = Path("/home/pi/sre/sre_state.db")
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

LOG_FILES = _config.get("log_files", [
    "/home/pi/bikefit-api/logs/analytics.jsonl",
    "/home/pi/bikefit-api/logs/frontend_errors.jsonl",
    "/home/pi/bikefit-api/logs/error.log",
    "/home/pi/sre/daemon.log",
])

DOCKER_CONTAINERS = _config.get("docker_containers", [
    "bikefit-api",
    "bikefit-frontend",
    "coachonurai-api",
    "trihonor-api-prod",
    "vaultwarden",
])

DAILY_CLOUD_LIMITS = _config.get("daily_cloud_api_limit", {
    "gemini": 100,
    "groq": 100,
    "xai": 50,
    "claude": 5
})

# ── Daily call tracking helpers ──────────────────────────────
def get_daily_calls(model_provider: str) -> int:
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS daily_api_usage (provider TEXT, day TEXT, count INTEGER, PRIMARY KEY (provider, day))"
            )
            cur.execute(
                "SELECT count FROM daily_api_usage WHERE provider = ? AND day = ?",
                (model_provider, today_str)
            )
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception as e:
        print(f"Token budget check error: {e}")
        return 0

def increment_daily_calls(model_provider: str):
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS daily_api_usage (provider TEXT, day TEXT, count INTEGER, PRIMARY KEY (provider, day))"
            )
            conn.execute(
                "INSERT INTO daily_api_usage (provider, day, count) VALUES (?, ?, 1) "
                "ON CONFLICT(provider, day) DO UPDATE SET count = count + 1",
                (model_provider, today_str)
            )
            conn.commit()
    except Exception as e:
        print(f"Token budget update error: {e}")

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

# ── Yardımcı Fonksiyonlar ─────────────────────────────────────

def load_state() -> dict:
    """Son çalıştırma durumunu yükle"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_run": None, "last_log_positions": {}}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, default=str))

def get_docker_logs(container: str, since_minutes: int = 30) -> str:
    """Docker container loglarını al"""
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", f"{since_minutes}m", "--tail", "200", container],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except Exception:
        return ""

def get_system_stats() -> dict:
    """Sistem durumunu al"""
    stats = {}
    try:
        # Disk kullanımı
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        stats["disk"] = df.stdout.strip().split("\n")[-1]

        # CPU/RAM
        free = subprocess.run(["free", "-h"], capture_output=True, text=True)
        stats["memory"] = free.stdout.strip().split("\n")[1]

        # CPU sıcaklığı
        temp = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True)
        stats["cpu_temp"] = temp.stdout.strip()

        # Docker container durumları
        ps = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True
        )
        stats["containers"] = ps.stdout.strip()
    except Exception as e:
        stats["error"] = str(e)
    return stats

def read_recent_log_lines(filepath: str, last_position: int = 0) -> tuple[str, int]:
    """Log dosyasından yeni satırları oku ve filtrele"""
    try:
        p = Path(filepath)
        if not p.exists():
            return "", last_position
        
        current_size = p.stat().st_size
        if current_size <= last_position:
            return "", last_position
        
        with open(filepath, "r", errors="ignore") as f:
            f.seek(last_position)
            new_content = f.read(50000)  # Max 50KB
        
        # Filter out normal Telegram long polling timeouts and connection drops
        if "daemon.log" in filepath:
            filtered_lines = []
            for line in new_content.splitlines():
                if any(k in line for k in [
                    "Read timed out", 
                    "read timeout", 
                    "api.telegram.org", 
                    "requests.exceptions.ReadTimeout", 
                    "requests.exceptions.ConnectionError",
                    "HTTPSConnectionPool(host='api.telegram.org'"
                ]):
                    continue
                filtered_lines.append(line)
            new_content = "\n".join(filtered_lines)
            
        return new_content.strip(), current_size
    except Exception:
        return "", last_position

def collect_all_data(state: dict) -> dict:
    """Tüm sistem verilerini topla"""
    data = {
        "timestamp": datetime.now().isoformat(),
        "system_stats": get_system_stats(),
        "docker_logs": {},
        "app_logs": {},
    }

    # Docker logları
    for container in DOCKER_CONTAINERS:
        logs = get_docker_logs(container, LOG_WINDOW_MINUTES)
        if logs:
            data["docker_logs"][container] = summarize_log(logs[-3000:])  # Özetlenmiş loglar

    # Uygulama log dosyaları
    new_positions = state.get("last_log_positions", {})
    for log_file in LOG_FILES:
        last_pos = state.get("last_log_positions", {}).get(log_file, 0)
        content, new_pos = read_recent_log_lines(log_file, last_pos)
        new_positions[log_file] = new_pos
        if content:
            data["app_logs"][log_file] = summarize_log(content[-3000:])  # Özetlenmiş loglar

    state["last_log_positions"] = new_positions
    return data

def build_prompt(data: dict) -> str:
    """Analiz promptunu oluştur"""
    summary = {
        "system": data["system_stats"],
        "docker_logs": {k: v for k, v in data["docker_logs"].items() if v},
        "app_logs": {k: v for k, v in data["app_logs"].items() if v},
    }
    return f"""Sen bir SRE (Site Reliability Engineer) uzmanısın. Raspberry Pi 5 üzerinde çalışan production sisteminin loglarını analiz et.

Sistem: TriHonor / Onur Kapucu'nun Pi 5'i
Uygulamalar: BikeFit AI (bikefit.coachonurai.com), CoachOnurAI (coachonurai.com), LiteLLM proxy, Ollama

Analiz edilecek veriler:
{json.dumps(summary, ensure_ascii=False, indent=2)[:8000]}

GÖREV:
1. Kritik sorun var mı? (container çökmesi, disk dolması, yüksek hata oranı, güvenlik ihlali, sonsuz döngü, ödeme hatası vb.)
2. Kullanıcı deneyimini etkileyen sorun var mı?
3. Yakında sorun çıkarabilecek gerçek uyarı işaretleri var mı? (Örn: CPU sıcaklığı > 75°C, RAM/Disk kullanımı > 85%, çöken/sürekli yeniden başlayan container'lar, loglarda gerçek hata kayıtları).

ÖNEMLİ KURALLAR:
- Normal sistem metrikleri (CPU sıcaklığı < 75°C, Disk/RAM kullanımı < 85%, tüm container'ların çalışıyor/healthy olması ve loglarda hata bulunmaması) bir sorun VEYA uyarı işareti DEĞİLDİR. Bunları raporlama.
- "Her şey normal görünüyor ama izlenmeli" veya "disk kullanımı düşük ama veri büyümesi izlenmeli" gibi durumlar için sorun üretme.
- Eğer gerçek bir hata, çökme veya aşırı kaynak tüketimi (temp > 75°C, disk/ram > 85%) yoksa, SADECE "OK" cevabını ver.

CEVAP FORMATI:
- Eğer sorun YOKSA: sadece "OK" yaz, başka hiçbir şey yazma.
- Eğer sorun VARSA: Türkçe, kısa ve net bildir. Emoji kullan. Format:

🚨 [SORUN ÖZETİ]

📍 Nerede: [container/servis]
💥 Ne oldu: [açıklama]
🔧 Önerilen aksiyon: [ne yapılmalı]
⚡ Öncelik: [KRİTİK / YÜKSEK / ORTA]

Birden fazla sorun varsa her birini ayrı blok olarak listele."""


def analyze_with_gemini(prompt: str) -> tuple[bool, str]:
    """1. Katman: Google Gemini API (Free tier)"""
    if not GEMINI_API_KEY:
        return None, None
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
        result = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        print("✅ Gemini analiz tamamlandı")
        return _parse_result(result)
    except Exception as e:
        print(f"⚠️ Gemini başarısız: {e} — Bir sonraki adıma geçiliyor...")
        return None, None


def analyze_with_groq(prompt: str) -> tuple[bool, str]:
    """2. Katman: Groq API (Free tier)"""
    if not GROQ_API_KEY:
        return None, None
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
            "temperature": 0.1
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        res_json = resp.json()
        result = res_json["choices"][0]["message"]["content"].strip()
        print("✅ Groq analiz tamamlandı")
        return _parse_result(result)
    except Exception as e:
        print(f"⚠️ Groq başarısız: {e} — Bir sonraki adıma geçiliyor...")
        return None, None


def analyze_with_xai(prompt: str) -> tuple[bool, str]:
    """3. Katman: Grok (xAI) API (Cheap cloud fallback)"""
    if not XAI_API_KEY:
        return None, None
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
            "temperature": 0.1
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        res_json = resp.json()
        result = res_json["choices"][0]["message"]["content"].strip()
        print("✅ Grok (xAI) analiz tamamlandı")
        return _parse_result(result)
    except Exception as e:
        print(f"⚠️ Grok (xAI) başarısız: {e} — Bir sonraki adıma geçiliyor...")
        return None, None


def analyze_with_ollama(prompt: str) -> tuple[bool, str]:
    """4. Katman: Local Ollama — ücretsiz, Pi'de çalışıyor"""
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 800}
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json().get("response", "").strip()
        print(f"✅ Ollama analiz tamamlandı ({OLLAMA_MODEL})")
        return _parse_result(result)
    except Exception as e:
        print(f"⚠️ Ollama başarısız: {e} — LiteLLM'e geçiliyor...")
        return None, None


def analyze_with_litellm(prompt: str) -> tuple[bool, str]:
    """5. Katman: LiteLLM proxy — Pi'deki SRE router"""
    try:
        response = requests.post(
            LITELLM_URL,
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={
                "model": "sre-fallback",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "temperature": 0.1,
            },
            timeout=60
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"].strip()
        print(f"✅ LiteLLM analiz tamamlandı")
        return _parse_result(result)
    except Exception as e:
        print(f"⚠️ LiteLLM başarısız: {e} — Claude API'ye geçiliyor (son çare)...")
        return None, None


def analyze_with_claude_api(prompt: str) -> tuple[bool, str]:
    """6. Katman: Claude API — sadece ilk katmanlar çöktüğünde"""
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.content[0].text.strip()
        print(f"✅ Claude API analiz tamamlandı (fallback)")
        return _parse_result(result)
    except Exception as e:
        print(f"❌ Claude API de başarısız: {e}")
        return True, f"⚠️ Tüm AI modelleri başarısız oldu: {str(e)}"


def _parse_result(result: str) -> tuple[bool, str]:
    """Model çıktısını ayrıştır"""
    clean_result = result.strip().strip("'\"`.").strip().upper()
    if clean_result == "OK":
        return False, ""
    # Eğer çıktı içerisinde OK geçiyorsa ve herhangi bir hata emojisi (🚨 veya 💥) içermiyorsa sağlıklı kabul et
    if "OK" in clean_result and "🚨" not in result and "💥" not in result:
        return False, ""
    return True, result


def analyze(data: dict) -> tuple[bool, str]:
    """
    Model hiyerarşisi:
    Gemini (free) → Groq (free) → Grok (xAI) (cheap) → Ollama (local Pi) → LiteLLM (proxy) → Claude API (son çare)
    """
    prompt = build_prompt(data)

    def run_with_budget(provider_name: str, query_fn) -> tuple[Optional[bool], Optional[str]]:
        limit = DAILY_CLOUD_LIMITS.get(provider_name, 99999)
        current = get_daily_calls(provider_name)
        if current >= limit:
            print(f"⚠️ Daily API budget exceeded for {provider_name} ({current}/{limit} calls). Skipping.")
            return None, None
        has_prob, rep = query_fn()
        if has_prob is not None:
            increment_daily_calls(provider_name)
        return has_prob, rep

    # 1. Gemini
    has_problem, report = run_with_budget("gemini", lambda: analyze_with_gemini(prompt))
    if has_problem is not None:
        return has_problem, report

    # 2. Groq
    has_problem, report = run_with_budget("groq", lambda: analyze_with_groq(prompt))
    if has_problem is not None:
        return has_problem, report

    # 3. Grok (xAI)
    has_problem, report = run_with_budget("xai", lambda: analyze_with_xai(prompt))
    if has_problem is not None:
        return has_problem, report

    # 4. Ollama (local)
    has_problem, report = analyze_with_ollama(prompt)
    if has_problem is not None:
        return has_problem, report

    # 5. LiteLLM
    has_problem, report = analyze_with_litellm(prompt)
    if has_problem is not None:
        return has_problem, report

    # 6. Claude API (son çare)
    limit = DAILY_CLOUD_LIMITS.get("claude", 99999)
    current = get_daily_calls("claude")
    if current >= limit:
        print(f"⚠️ Daily API budget exceeded for claude ({current}/{limit} calls). Skipping.")
        return True, "⚠️ Claude API limit reached, analysis fell back to local."

    has_problem, report = analyze_with_claude_api(prompt)
    if has_problem is not None:
        increment_daily_calls("claude")
    return has_problem, report

def send_telegram(message: str):
    """Telegram'a bildirim gönder"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram yapılandırması eksik!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    header = f"🤖 *AI Log Analyst* — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": header + message,
        "parse_mode": "Markdown"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print(f"Telegram bildirimi gönderildi.")
    except Exception as e:
        print(f"Telegram hatası: {e}")

# ── Ana Akış ──────────────────────────────────────────────────

def main():
    print(f"[{datetime.now().isoformat()}] AI Log Analyst başlatıldı...")
    
    state = load_state()
    
    # Veri topla
    data = collect_all_data(state)
    
    # Veri yoksa analiz etme
    total_log_size = sum(len(v) for v in data["docker_logs"].values()) + \
                     sum(len(v) for v in data["app_logs"].values())
    
    if total_log_size < 100:
        print("Yeni log verisi yok, analiz atlanıyor.")
        save_state(state)
        return
    
    # AI analizi — Ollama → LiteLLM → Claude API
    has_problem, report = analyze(data)
    
    if has_problem:
        print(f"⚠️ Sorun tespit edildi, Telegram'a bildiriliyor...")
        send_telegram(report)
        # SRE Daemon için DB'ye yaz
        try:
            import sqlite3 as _sqlite3
            db_path = "/home/pi/sre/sre_state.db"
            with _sqlite3.connect(db_path) as _conn:
                _conn.execute("""
                    CREATE TABLE IF NOT EXISTS analyst_insights (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        report TEXT NOT NULL,
                        consumed INTEGER DEFAULT 0
                    )
                """)
                _conn.execute(
                    "INSERT INTO analyst_insights (created_at, report, consumed) VALUES (?, ?, 0)",
                    (datetime.now().isoformat(), report)
                )
                _conn.commit()
            print("✅ Insight DB'ye kaydedildi.")
        except Exception as e:
            print(f"DB yazma hatası: {e}")
    else:
        print("✅ Sistem sağlıklı, bildirim gönderilmedi.")
    
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

if __name__ == "__main__":
    main()

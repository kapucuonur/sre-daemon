#!/usr/bin/env python3
"""
SRE Daemon v3 — Total System Observer (Pi 5)
=============================================
v2'den farkı: Dosya tabanlı log okuma tamamen kaldırıldı.
Artık Pi OS'un sinir sistemine doğrudan bağlı:

  Kaynak 1 — Systemd Journal (journalctl -f -p err -o json)
    → Kernel, donanım (Hailo AI modülü, USB kamera), systemd servisleri,
      BikeFit-API, sre-daemon, nginx, PostgreSQL, vb.

  Kaynak 2 — Docker Event Monitor
    → trihonor-api-prod, coachonurai-api ve tüm container'lar.
    → `docker events` ile yeni hata/çökme anında yakalanır.
    → Çöken container'dan son N satır log çekilip analiz edilir.

  Rate Limiter:
    → Aynı (servis, hata_özeti) çifti 120 saniye içinde tekrar gelirse atlanır.
    → Aynı Docker container'dan burst gelirse 60sn beklenir.

  Proje Etiketleme (otomatik):
    → journald _SYSTEMD_UNIT veya SYSLOG_IDENTIFIER'dan türetilir.
    → Docker: container adından → [TriHonor-API], [AI-Coach], vb.
    → Kernel mesajları: [Kernel-HW]
    → Bilinmeyenler: [System]

Hiyerarşik LLM Eskalasyon (v2 ile aynı):
  1. Mac açık → Mac Ollama qwen2.5-coder:32b
  2. Mac kapalı → Pi Ollama qwen2.5-coder:7b
  3. 3 fail → Anthropic Claude Sonnet 4.6

Güvenlik:
  - API anahtarı yalnızca os.getenv ile okunur
  - Loglar hiçbir zaman kimlik bilgisi içermez
  - subprocess komutları sabit argümanlarla çalıştırılır (enjeksiyon yok)
  - Dış ağ: yalnızca api.anthropic.com (HTTPS/TLS)
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────
# Yapılandırma — .env'den okunur, ASLA hardcode değil
# ─────────────────────────────────────────────
MAC_IP            = os.getenv("MAC_IP", "")
MAC_OLLAMA_URL    = f"http://{MAC_IP}:11434"
PI_OLLAMA_URL     = os.getenv("PI_OLLAMA_URL", "http://localhost:11434")
HEAL_LOG          = os.getenv("HEAL_LOG", "/home/pi/sre/heal_log.jsonl")
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
MAX_LOCAL_TRIES   = 3
MAC_CHECK_TIMEOUT = 3
OLLAMA_TIMEOUT    = 120
ANTHROPIC_TIMEOUT = 60

# Rate limiter: (servis, hata_özeti) → son gönderim timestamp
RATE_LIMIT_SECONDS = 120
DOCKER_BURST_LIMIT = 60   # aynı container'dan burst

# Gürültü filtresi: bunları LLM'e GÖNDERMEYİN
NOISE_PATTERNS = re.compile(
    r"(DEBUG|audit\(|systemd-logind|NetworkManager.*state|"
    r"DHCP|avahi|dbus-daemon|Bluetooth|btusb|rfkill|"
    r"CRON|anacron|logrotate|sre-daemon|sre-bridge)",
    re.IGNORECASE
)

# Proje etiket haritası — container/servis adı → etiket
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


def _validate_env():
    errors = []
    if not MAC_IP:
        errors.append("MAC_IP tanımlanmamış")
    if not ANTHROPIC_KEY or ANTHROPIC_KEY == "BURAYA_YENI_ANAHTARINIZI_GIRIN":
        errors.append("ANTHROPIC_API_KEY tanımlanmamış veya placeholder")
    if errors:
        for e in errors:
            logging.critical("ENV HATASI: %s", e)
        sys.exit(1)


def _tag_from_unit(unit: str, identifier: str, message: str) -> str:
    """journald _SYSTEMD_UNIT ve SYSLOG_IDENTIFIER'dan proje etiketi türetir."""
    combined = f"{unit} {identifier} {message}".lower()
    for keyword, tag in PROJECT_MAP.items():
        if keyword in combined:
            return tag
    # Servis adını direkt kullan
    if unit and unit != "-":
        clean = unit.replace(".service", "").replace(".scope", "")
        return f"[{clean[:20]}]"
    if identifier and identifier != "-":
        return f"[{identifier[:20]}]"
    return "[System]"


def _tag_from_container(container_name: str) -> str:
    """Docker container adından proje etiketi türetir."""
    name_lower = container_name.lower()
    for keyword, tag in PROJECT_MAP.items():
        if keyword in name_lower:
            return tag
    return f"[{container_name[:20]}]"


# ─────────────────────────────────────────────
# Loglama
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler("/home/pi/sre/daemon.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("sre-daemon")


# ─────────────────────────────────────────────
# Rate Limiter
# ─────────────────────────────────────────────
class RateLimiter:
    """Aynı hata/servis kombinasyonunu belirli süre içinde sadece 1 kez iletir."""

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
            # Bellek temizliği
            if len(self._seen) > 500:
                oldest = min(self._seen, key=self._seen.get)
                del self._seen[oldest]
            return True


# ─────────────────────────────────────────────
# Kaynak 1: Systemd Journal Watcher
# ─────────────────────────────────────────────
class JournalWatcher:
    """
    journalctl -f -p err -o json ile Pi'nin tüm systemd/kernel
    error akışını dinler.

    Güvenlik: subprocess.Popen sabit argümanlarla çalıştırılır,
    kullanıcı girdisi komut satırına hiçbir şekilde geçmez.
    """

    # Sadece priority 0-3 (emerg/alert/crit/err) dinliyoruz
    # -n 0 ile başlangıçta eski logların tetiklenmesi önlenir
    JOURNAL_CMD = [
        "journalctl",
        "-f",           # follow (canlı akış)
        "-n", "0",      # geçmiş logları atla
        "-p", "err",    # err ve üstü (0-3)
        "-o", "json",   # makine okunabilir çıktı
        "--no-pager",
        "--no-hostname",
    ]

    def __init__(self, rate_limiter: RateLimiter, callback):
        self.rate_limiter = rate_limiter
        self.callback = callback
        self._stop_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        thread = threading.Thread(target=self._watch, name="journal-watcher", daemon=True)
        thread.start()
        logger.info("JournalWatcher başlatıldı (journalctl -f -p err)")

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
            except (OSError, ValueError) as e:
                logger.error("JournalWatcher hata: %s — 10sn sonra yeniden deniyor", str(e))
                time.sleep(10)
            finally:
                if self._proc:
                    try:
                        self._proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()

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

        # Priority 4 (warning) ve üstünü filtrele — sadece 0-3 işle
        if priority > 3:
            return

        # Gürültü filtresi
        if NOISE_PATTERNS.search(f"{unit} {ident} {message}"):
            return

        # Güvenlik: mesaj maksimum 2000 karakter
        safe_msg = str(message)[:2000]
        if not safe_msg:
            return

        tag = _tag_from_unit(unit, ident, safe_msg)
        rate_key = f"journal:{unit}:{safe_msg[:80]}"

        if not self.rate_limiter.should_process(rate_key):
            logger.debug("Rate limit — atlandı: %s", rate_key[:60])
            return

        priority_label = {0: "EMERG", 1: "ALERT", 2: "CRIT", 3: "ERR"}.get(priority, "ERR")
        tagged = f"{tag} [{priority_label}][{ident}] {safe_msg}"

        logger.info("Journal hata yakalandı %s: %s", tag, safe_msg[:100])
        self.callback(tagged, tag)


# ─────────────────────────────────────────────
# Kaynak 2: Docker Event Watcher
# ─────────────────────────────────────────────
class DockerWatcher:
    """
    `docker events --filter type=container` ile container
    die/oom/kill olaylarını dinler. Olay gelince container'ın
    son loglarını çekip orchestrator'a iletir.

    Güvenlik: container ID doğrulama ile injection önlenir.
    """

    CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{64}$")  # sadece hex ID kabul et

    DOCKER_EVENTS_CMD = [
        "docker", "events",
        "--filter", "type=container",
        "--filter", "event=die",
        "--filter", "event=oom",
        "--filter", "event=kill",
        "--format", "{{json .}}",
    ]

    def __init__(self, rate_limiter: RateLimiter, callback):
        self.rate_limiter = rate_limiter
        self.callback = callback
        self._stop_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        thread = threading.Thread(target=self._watch, name="docker-watcher", daemon=True)
        thread.start()
        logger.info("DockerWatcher başlatıldı (docker events: die/oom/kill)")

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
            except (OSError, ValueError) as e:
                logger.error("DockerWatcher hata: %s — 15sn sonra yeniden deniyor", str(e))
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

        # Güvenlik: container ID yalnızca hex karakterler içermeli
        # (inspect/logs komutuna geçmeden önce doğrula)
        if not self.CONTAINER_ID_RE.match(container_id):
            logger.warning("Geçersiz container ID formatı, atlandı")
            return

        tag = _tag_from_container(container_name)
        rate_key = f"docker:{container_name}:{action}"

        if not self.rate_limiter.should_process(rate_key, DOCKER_BURST_LIMIT):
            logger.debug("Docker rate limit — atlandı: %s", rate_key)
            return

        logger.warning("Docker olayı: %s %s → %s", action.upper(), container_name, tag)

        # Son 30 satır logu çek
        logs = self._fetch_logs(container_id, container_name)
        tagged = f"{tag} [DOCKER-{action.upper()}] Container '{container_name}' çöktü.\nSon loglar:\n{logs}"
        self.callback(tagged, tag)

    def _fetch_logs(self, container_id: str, container_name: str) -> str:
        """Güvenlik: container_id doğrulanmış hex string. Eğer container silinmişse isme fallback yapar."""
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", "30", container_id],
                capture_output=True, text=True, timeout=10
            )
            output = (result.stdout + result.stderr).strip()
            
            # Eğer container bulunamadıysa isme fallback yap
            if "No such container" in output or not output:
                if container_name and re.match(r"^[a-zA-Z0-9_-]+$", container_name):
                    result = subprocess.run(
                        ["docker", "logs", "--tail", "30", container_name],
                        capture_output=True, text=True, timeout=10
                    )
                    output = (result.stdout + result.stderr).strip()

            # Sadece ERROR içeren satırları filtrele
            error_lines = [l for l in output.splitlines()
                           if re.search(r"error|exception|traceback|fatal|fail", l, re.IGNORECASE)]
            return "\n".join(error_lines[-20:]) if error_lines else output[-1000:]
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"Log alınamadı: {str(e)}"


# ─────────────────────────────────────────────
# LLM İstemcileri (v2 ile aynı)
# ─────────────────────────────────────────────
class MacChecker:
    def is_mac_online(self) -> bool:
        if not MAC_IP:
            return False
        try:
            resp = requests.get(f"{MAC_OLLAMA_URL}/api/tags", timeout=MAC_CHECK_TIMEOUT)
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False


class OllamaClient:
    def query(self, base_url: str, model: str, prompt: str) -> Optional[str]:
        allowed = {MAC_OLLAMA_URL, PI_OLLAMA_URL}
        if base_url not in allowed:
            logger.error("İzin verilmeyen URL reddedildi: %s", base_url)
            return None
        try:
            resp = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt,
                      "stream": False, "options": {"temperature": 0.2}},
                timeout=OLLAMA_TIMEOUT,
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.exceptions.ConnectionError:
            logger.warning("Ollama bağlantı hatası: %s", base_url)
        except requests.exceptions.Timeout:
            logger.warning("Ollama timeout: %s (model: %s)", base_url, model)
        except requests.exceptions.RequestException as e:
            logger.error("Ollama istek hatası: %s", str(e))
        return None


class AnthropicClient:
    def query(self, prompt: str) -> Optional[str]:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key or key == "BURAYA_YENI_ANAHTARINIZI_GIRIN":
            logger.critical("Anthropic API anahtarı geçersiz — eskalasyon iptal")
            return None
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-5", "max_tokens": 2048,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=ANTHROPIC_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except requests.exceptions.RequestException as e:
            logger.error("Anthropic API hatası: %s", str(e))
        return None


# ─────────────────────────────────────────────
# Healing Orchestrator
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Healing Orchestrator (v4 - Auto-Remediation Engine)
# ─────────────────────────────────────────────
class HealingOrchestrator:
    def __init__(self):
        self.mac_checker  = MacChecker()
        self.ollama       = OllamaClient()
        self.anthropic    = AnthropicClient()

    def handle_error(self, tagged_line: str, project_tag: str):
        threading.Thread(
            target=self._heal, args=(tagged_line, project_tag),
            name=f"healer-{project_tag}", daemon=True
        ).start()

    def _build_prompt(self, tagged_line: str, project_tag: str) -> str:
        project_name = project_tag.strip("[]")
        return (
            f"Sen bir kıdemli Linux SRE ve backend geliştiricisisin.\n"
            f"Sistem: Raspberry Pi 5 | Proje/Servis: {project_name}\n\n"
            "Pi üzerindeki bilinen proje dizinleri ve docker-compose yolları şunlardır:\n"
            "- BikeFit-API (bikefit-api): Proje dizini: /home/pi/bikefit-api | docker-compose dizini: /home/pi/bikefit (docker-compose.yml burada yer alır)\n"
            "- AI-Coach (coachonurai-api): Proje dizini: /home/pi/projects/AI-Coach | docker-compose dizini: /home/pi/projects/AI-Coach (docker-compose.yml burada yer alır)\n"
            "- TriHonor-API (trihonor-api-prod): Proje dizini: /home/pi/TriHonor-API | docker-compose dizini: /home/pi/TriHonor-API (docker-compose.prod.yml burada yer alır)\n\n"
            "Aşağıdaki sistem hata mesajını analiz et:\n"
            "1. Kök nedeni açıkla (1-2 cümle)\n"
            "2. Tekrarlanmaması için önlem öner\n"
            "3. Eğer hata otomatik olarak düzeltilebiliyorsa (örn: eksik python kütüphanesini requirements.txt'e ekleme, konteyner veya servis restart etme), bunu eylemler ('actions') dizisi olarak tanımla.\n\n"
            "Eylemler türleri:\n"
            "- 'append': Bir dosyaya yeni bir satır eklemek için (örn: target='/home/pi/bikefit-api/requirements.txt', payload='slowapi')\n"
            "- 'shell': Güvenli bir komut çalıştırmak için (örn: target='/home/pi/bikefit', payload='docker compose up -d --build bikefit-api').\n"
            "  NOT: Shell komutlarında 'target' alanı docker-compose.yml dosyasının bulunduğu DİZİN olmalıdır (yukarıdaki dizin haritasına dikkat et!). Shell komutları yalnızca docker compose, docker restart veya systemctl restart/start komutları olmalıdır. Güvenli olmayan veya izin verilmeyen hiçbir komut çalıştırma!\n\n"
            f"HATA:\n{tagged_line[:1500]}\n\n"
            "JSON formatında yanıt ver. Yanıtın mutlaka geçerli bir JSON olmalıdır ve kod blokları içermemelidir. Örnek şema:\n"
            "{\n"
            '  "root_cause": "Kök neden açıklaması",\n'
            '  "prevention": "Önlem açıklaması",\n'
            '  "actions": [\n'
            '    {"type": "append", "target": "/home/pi/bikefit-api/requirements.txt", "payload": "slowapi"},\n'
            '    {"type": "shell", "target": "/home/pi/bikefit", "payload": "docker compose up -d --build bikefit-api"}\n'
            '  ]\n'
            "}"
        )

    def _execute_actions(self, result_str: str, project_tag: str) -> list:
        """JSON yanıtından eylemleri parse edip güvenli bir şekilde çalıştırır."""
        executed = []
        try:
            # Markdown json bloklarını temizle (varsa)
            cleaned = result_str.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            data = json.loads(cleaned)
            actions = data.get("actions", [])
            if not isinstance(actions, list):
                return [{"status": "failed", "error": "actions liste değil"}]

            for act in actions:
                act_type = act.get("type")
                target = act.get("target", "").strip()
                payload = act.get("payload", "").strip()

                if not act_type or not target or not payload:
                    continue

                if act_type == "append":
                    # Güvenlik doğrulaması: target /home/pi ile başlamalı, .. içermemeli, /sre/ içermemeli
                    target_path = Path(target).resolve()
                    if not target.startswith("/home/pi/") or ".." in target or "/sre/" in target:
                        logger.warning("%s Güvenlik engeli: Geçersiz append hedefi: %s", project_tag, target)
                        executed.append({"type": "append", "target": target, "status": "rejected", "reason": "güvenlik engeli"})
                        continue
                    try:
                        # Son satırın yeni satır karakteriyle bitmesini sağla ve ekle
                        if target_path.exists():
                            with open(target_path, "a", encoding="utf-8") as f:
                                if target_path.stat().st_size > 0:
                                    with open(target_path, "rb+") as f_binary:
                                        f_binary.seek(-1, 2)
                                        last_char = f_binary.read(1)
                                        if last_char != b'\n':
                                            f.write("\n")
                                f.write(payload + "\n")
                        else:
                            with open(target_path, "w", encoding="utf-8") as f:
                                f.write(payload + "\n")
                        logger.info("%s Otomatik Onarım: %s dosyasına eklendi: %s", project_tag, target, payload)
                        executed.append({"type": "append", "target": target, "payload": payload, "status": "success"})
                    except OSError as e:
                        logger.error("%s Dosya ekleme hatası: %s", project_tag, str(e))
                        executed.append({"type": "append", "target": target, "payload": payload, "status": "failed", "error": str(e)})

                elif act_type == "shell":
                    # Güvenlik doğrulaması: target /home/pi ile başlamalı, .. içermemeli, /sre/ içermemeli
                    if not target.startswith("/home/pi/") or ".." in target or "/sre/" in target:
                        logger.warning("%s Güvenlik engeli: Geçersiz shell dizini: %s", project_tag, target)
                        executed.append({"type": "shell", "target": target, "status": "rejected", "reason": "güvenlik engeli"})
                        continue

                    # Komut whitelisting
                    allowed_patterns = [
                        r"^docker\s+compose\s+(up\s+-d\s+--build|restart|up\s+-d)(\s+[a-zA-Z0-9_-]+)?$",
                        r"^docker\s+restart\s+[a-zA-Z0-9_-]+$",
                        r"^systemctl\s+(restart|start)\s+[a-zA-Z0-9_-]+$"
                    ]
                    is_allowed = any(re.match(pat, payload) for pat in allowed_patterns)
                    
                    if not is_allowed or any(char in payload for char in [";", "|", "&", "`", "$", "\n", "\r"]):
                        logger.warning("%s Güvenlik engeli: İzin verilmeyen shell komutu: %s", project_tag, payload)
                        executed.append({"type": "shell", "target": target, "payload": payload, "status": "rejected", "reason": "güvenlik engeli"})
                        continue

                    try:
                        logger.info("%s Otomatik Onarım: Cwd: %s, Komut çalıştırılıyor: %s", project_tag, target, payload)
                        cmd_args = payload.split()
                        if cmd_args[0] == "systemctl":
                            cmd_args = ["sudo", "systemctl"] + cmd_args[1:]
                            
                        result = subprocess.run(
                            cmd_args,
                            cwd=target,
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        if result.returncode == 0:
                            logger.info("%s Komut başarıyla çalıştırıldı: %s", project_tag, payload)
                            executed.append({"type": "shell", "target": target, "payload": payload, "status": "success", "stdout": result.stdout[:500]})
                        else:
                            logger.error("%s Komut başarısız oldu (code %d): %s\nStderr: %s", project_tag, result.returncode, payload, result.stderr)
                            executed.append({"type": "shell", "target": target, "payload": payload, "status": "failed", "code": result.returncode, "error": result.stderr[:500]})
                    except (subprocess.SubprocessError, OSError) as e:
                        logger.error("%s Komut çalıştırma hatası: %s", project_tag, str(e))
                        executed.append({"type": "shell", "target": target, "payload": payload, "status": "failed", "error": str(e)})

        except json.JSONDecodeError as e:
            logger.error("%s JSON parse hatası: %s", project_tag, str(e))
            executed.append({"status": "failed", "error": f"JSON parse hatası: {str(e)}"})
        except Exception as e:
            logger.error("%s Beklenmedik hata: %s", project_tag, str(e))
            executed.append({"status": "failed", "error": str(e)})

        return executed

    def _heal(self, tagged_line: str, project_tag: str):
        prompt = self._build_prompt(tagged_line, project_tag)
        result = None
        source = None
        success = False

        mac_online = self.mac_checker.is_mac_online()
        logger.info("%s Mac: %s", project_tag,
                    "AÇIK ✅" if mac_online else "KAPALI 🌙")

        for attempt in range(1, MAX_LOCAL_TRIES + 1):
            if mac_online:
                logger.info("%s [%d/%d] Mac Ollama qwen2.5-coder:32b...",
                            project_tag, attempt, MAX_LOCAL_TRIES)
                result = self.ollama.query(MAC_OLLAMA_URL, "qwen2.5-coder:32b", prompt)
                source = "mac-ollama/qwen2.5-coder:32b"
            else:
                logger.info("%s [%d/%d] Pi Ollama qwen2.5-coder:7b...",
                            project_tag, attempt, MAX_LOCAL_TRIES)
                result = self.ollama.query(PI_OLLAMA_URL, "qwen2.5-coder:7b", prompt)
                source = "pi-ollama/qwen2.5-coder:7b"

            if result:
                success = True
                break
            logger.warning("%s [%d/%d] başarısız.", project_tag, attempt, MAX_LOCAL_TRIES)
            time.sleep(5 * attempt)

        if not success:
            logger.warning("%s ANTHROPIC ESKALASYON...", project_tag)
            result = self.anthropic.query(prompt)
            source = "anthropic/claude-sonnet-4-5"
            success = result is not None

        # Auto-Remediation (Eylemleri Çalıştır)
        actions_status = []
        if success and result:
            logger.info("%s Onarım kararı alındı. Eylemler çözümleniyor...", project_tag)
            actions_status = self._execute_actions(result, project_tag)

        self._write_heal_log(tagged_line, result, source, success, project_tag, actions_status)
        if success:
            logger.info("%s ✅ Analiz tamamlandı → %s", project_tag, source)
        else:
            logger.critical("%s ❌ TÜM KAYNAKLAR BAŞARISIZ.", project_tag)

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


# ─────────────────────────────────────────────
# Ana Daemon
# ─────────────────────────────────────────────
def main():
    logger.info("=" * 65)
    logger.info("SRE Daemon v4 — Auto-Remediation Observer başlatılıyor...")
    logger.info("Mac IP: %s", MAC_IP)
    logger.info("Kaynaklar:")
    logger.info("  📡 Systemd Journal  (journalctl -f -p err)")
    logger.info("  🐳 Docker Events    (die / oom / kill)")
    logger.info("Rate Limiter: %ds journal, %ds docker burst", RATE_LIMIT_SECONDS, DOCKER_BURST_LIMIT)
    logger.info("=" * 65)

    _validate_env()
    Path(HEAL_LOG).parent.mkdir(parents=True, exist_ok=True)

    rate_limiter = RateLimiter()
    orchestrator = HealingOrchestrator()

    journal_watcher = JournalWatcher(rate_limiter, orchestrator.handle_error)
    docker_watcher  = DockerWatcher(rate_limiter, orchestrator.handle_error)

    journal_watcher.start()
    docker_watcher.start()

    logger.info("Total System Observer aktif — Pi 5 tamamen izleniyor.")

    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Kapatma sinyali (%s). Durduruluyor...", signum)
        journal_watcher.stop()
        docker_watcher.stop()
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while not stop_event.is_set():
        stop_event.wait(timeout=60)

    logger.info("SRE Daemon v3 durduruldu.")


if __name__ == "__main__":
    main()

import re
import json
import time
import threading
import subprocess
import logging
from typing import Optional, Callable
from .base import BaseWatcher

logger = logging.getLogger("sre-daemon.journal-watcher")

class JournalWatcher(BaseWatcher):
    JOURNAL_CMD = [
        "journalctl", "-f", "-n", "0", "-p", "err", "-o", "json",
        "--no-pager", "--no-hostname"
    ]

    def __init__(self, rate_limiter, callback: Callable[[str, str], None], project_map: dict, noise_patterns_regex: re.Pattern):
        self.rate_limiter = rate_limiter
        self.callback = callback
        self.project_map = project_map
        self.noise_patterns = noise_patterns_regex
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
        if self.noise_patterns.search(f"{unit} {ident} {message}"):
            return

        safe_msg = str(message)[:2000]
        if not safe_msg:
            return

        # Derive project tag
        combined = f"{unit} {ident} {safe_msg}".lower()
        tag = "[System]"
        for keyword, mapped_tag in self.project_map.items():
            if keyword in combined:
                tag = mapped_tag
                break

        rate_key = f"journal:{unit}:{safe_msg[:80]}"
        if not self.rate_limiter.should_process(rate_key):
            return

        priority_label = {0: "EMERG", 1: "ALERT", 2: "CRIT", 3: "ERR"}.get(priority, "ERR")
        tagged = f"{tag} [{priority_label}][{ident}] {safe_msg}"
        self.callback(tagged, tag)

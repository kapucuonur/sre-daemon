import re
import json
import time
import threading
import subprocess
import logging
from typing import Optional, Callable
from .base import BaseWatcher

logger = logging.getLogger("sre-daemon.docker-watcher")

class DockerWatcher(BaseWatcher):
    CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{64}$")
    DOCKER_EVENTS_CMD = [
        "docker", "events", "--filter", "type=container",
        "--filter", "event=die", "--filter", "event=oom", "--filter", "event=kill",
        "--format", "{{json .}}"
    ]

    def __init__(self, rate_limiter, callback: Callable[[str, str], None], project_map: dict, docker_burst_limit: int = 3):
        self.rate_limiter = rate_limiter
        self.callback = callback
        self.project_map = project_map
        self.docker_burst_limit = docker_burst_limit
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
        for keyword, mapped_tag in self.project_map.items():
            if keyword in name_lower:
                tag = mapped_tag
                break

        rate_key = f"docker:{container_name}:{action}"
        if not self.rate_limiter.should_process(rate_key, self.docker_burst_limit):
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

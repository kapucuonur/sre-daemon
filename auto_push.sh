#!/bin/bash
cd /home/pi/sre
git add sre_daemon.py ai_log_analyst.py learned_patterns.json requirements.txt sre_incidents.md
git diff --cached --quiet && exit 0  # değişiklik yoksa çık
git commit -m "auto: $(date '+%Y-%m-%d %H:%M') — daemon update"
git push origin main

#!/bin/bash
# Her 30 saniyede journald'dan ERROR içeren satırları ilgili dosyalara yazar
while true; do
  # BikeFit
  journalctl -u bikefit-api --since '30 seconds ago' --no-pager -q 2>/dev/null |     grep -iE 'error|exception|traceback|500' >> /home/pi/bikefit-api/logs/error.log 2>/dev/null || true
  
  # TriHonor (Docker)
  docker logs trihonor-api-prod --since 30s 2>&1 |     grep -iE 'error|exception|traceback|500' >> /home/pi/TriHonor-API/logs/error.log 2>/dev/null || true
  
  # CoachOnurAI (Docker)
  docker logs coachonurai-api --since 30s 2>&1 |     grep -iE 'error|exception|traceback|500' >> /home/pi/AI-Coach/backend/logs/error.log 2>/dev/null || true
  
  sleep 30
done

#!/usr/bin/env bash
# ============================================================
# Pi 5 SRE Daemon — Kurulum Script'i (Mac'ten çalıştırılır)
# Kullanım: bash ~/litellm/pi_files/deploy_to_pi.sh
# Gereksinim: SSH erişimi aktif (ssh pi@168.192.1.116)
# ============================================================

set -euo pipefail

PI_HOST="pi@192.168.1.116"
PI_SRE_DIR="/home/pi/sre"
LOCAL_FILES="$(dirname "$0")"

echo "╔══════════════════════════════════════════╗"
echo "║   SRE Daemon — Pi 5 Kurulumu Başlıyor   ║"
echo "╚══════════════════════════════════════════╝"

# 1. Pi'de sre/ klasörü oluştur
echo "→ [1/5] Pi'de $PI_SRE_DIR klasörü oluşturuluyor..."
sshpass -p "pi" ssh -o StrictHostKeyChecking=no "$PI_HOST" "mkdir -p $PI_SRE_DIR"

# 2. Python daemon dosyasını kopyala
echo "→ [2/5] sre_daemon.py aktarılıyor..."
sshpass -p "pi" scp -o StrictHostKeyChecking=no "$LOCAL_FILES/sre_daemon.py" "$PI_HOST:$PI_SRE_DIR/sre_daemon.py"

# 3. requirements.txt kopyala ve kur
echo "→ [3/5] Python bağımlılıkları kuruluyor..."
sshpass -p "pi" scp -o StrictHostKeyChecking=no "$LOCAL_FILES/requirements.txt" "$PI_HOST:$PI_SRE_DIR/requirements.txt"
sshpass -p "pi" ssh -o StrictHostKeyChecking=no "$PI_HOST" "pip3 install -r $PI_SRE_DIR/requirements.txt --break-system-packages --quiet"

# 4. .env dosyasını kopyala (kullanıcı doldurmuşsa)
echo "→ [4/5] .env dosyası kontrol ediliyor..."
if grep -q "BURAYA" "$LOCAL_FILES/.env"; then
  echo "⚠️  UYARI: .env dosyasındaki placeholder değerler henüz doldurulmamış!"
  echo "    Lütfen şu iki değeri girin:"
  echo "    MAC_IP=192.168.1.xxx"
  echo "    ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
  echo "    Dosya: $LOCAL_FILES/.env"
  echo "    Düzenledikten sonra bu script'i tekrar çalıştırın."
  exit 1
fi
sshpass -p "pi" scp -o StrictHostKeyChecking=no "$LOCAL_FILES/.env" "$PI_HOST:$PI_SRE_DIR/.env"
sshpass -p "pi" ssh -o StrictHostKeyChecking=no "$PI_HOST" "chmod 600 $PI_SRE_DIR/.env"  # Yalnızca pi kullanıcısı okuyabilsin

# 5. systemd servisini kur ve etkinleştir
echo "→ [5/5] systemd servisi kuruluyor..."
sshpass -p "pi" scp -o StrictHostKeyChecking=no "$LOCAL_FILES/sre-daemon.service" "$PI_HOST:/tmp/sre-daemon.service"
sshpass -p "pi" ssh -o StrictHostKeyChecking=no "$PI_HOST" "echo pi | sudo -S mv /tmp/sre-daemon.service /etc/systemd/system/sre-daemon.service && \
                echo pi | sudo -S systemctl daemon-reload && \
                echo pi | sudo -S systemctl enable sre-daemon && \
                echo pi | sudo -S systemctl restart sre-daemon"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         ✅ Kurulum Tamamlandı!           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Durumu kontrol et: ssh $PI_HOST 'systemctl status sre-daemon'"
echo "Logları izle:      ssh $PI_HOST 'journalctl -u sre-daemon -f'"
echo "Onarım kayıtları:  ssh $PI_HOST 'tail -f $PI_SRE_DIR/heal_log.jsonl'"

#!/bin/bash
echo "Sistem kontrolü başlıyor..."
# 1. Konfigürasyonu düzelt
sed -i 's/"db_type": "sqlite"/"db_type": "postgresql"/' ~/sre/config.json
# 2. Yetkileri tazele
sudo chown -R pi:pi /home/pi/sre
# 3. Servisi yeniden başlat
sudo systemctl restart sre-daemon
echo "Sistem kontrolü tamamlandı."

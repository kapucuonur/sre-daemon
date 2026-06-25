# SRE Daemon — AI Self-Healing Engine (v5.1)

> An advanced, production-grade AI-powered self-healing SRE daemon for Raspberry Pi 5. It monitors systemd journals and Docker events in real-time, diagnoses errors using a 6-tier hierarchical LLM fallback stack, and executes safe auto-remediation with a Human-in-the-Loop (HITL) approval gateway.

---

## Mimarisi (Architecture)

```
Raspberry Pi 5
│
├── systemd journal (priority 0-3: emerg/alert/crit/err)
├── Docker events (die / oom / kill)
│
└── SRE Daemon (sre_daemon.py)
      │
      ├── 1. SQLite HITL State Machine (sre_state.db) -> Track approvals
      ├── 2. Independent Watchdog & Heartbeat -> Automatic rollback & recovery
      │
      └── 3. 6-Tier LLM Fallback Pipeline
            ├── 1. MacBook Ollama (Local)  --> qwen2.5-coder:32b (Free)
            ├── 2. Google Gemini API       --> gemini-2.5-flash (Free Cloud)
            ├── 3. Groq API                --> llama-3.3-70b-versatile (Free Cloud)
            ├── 4. Grok (xAI) API          --> grok-2-1212 (Cheap Cloud)
            ├── 5. Local Pi Ollama         --> qwen2.5-coder:7b (Offline Fallback)
            └── 6. Anthropic Claude API    --> claude-sonnet-4-6 (Expensive Fallback)
```

---

## Nasıl Çalışır? (How It Works)

1. **İzleme (Monitor)**: `systemd journal` ve Docker olayları gerçek zamanlı olarak dinlenir.
2. **Sorun Tespiti (Detect)**: Hatalar ve container çökmeleri yakalanarak filtrelerden geçirilir.
3. **Analiz (Analyze)**: Hata, 6 aşamalı LLM hiyerarşisine gönderilir. İlk çalışan model hatayı analiz eder, kök nedeni bulur ve bir aksiyon planı (`actions`) hazırlar.
4. **Risk Sınıflandırması (Risk Assessment)**:
   * **Düşük / Orta Risk (Low/Medium)**: Konteyner restartı, `requirements.txt` güncelleme vb. işlemler **otomatik** olarak uygulanır.
   * **Yüksek / Kritik Risk (High/Critical)**: Kod yazma veya değiştirme işlemleri SQLite veri tabanına yazılır ve **Telegram Onay İstemi** tetiklenir.
5. **Onay & Düzeltme (Remediate)**: Telegram üzerinden gelen `Kabul` / `Red` butonları ile işlem onaylanırsa kod atomik olarak güncellenir ve sistem ayağa kaldırılır.
6. **Watchdog Koruması (Watchdog)**: Değişiklik sonrasında daemon kilitlenirse veya çökme döngüsüne girerse, bağımsız watchdog süreci otomatik olarak `git rollback` yapar.

---

## Örnek Arayüz ve Bildirimler (Telegram HITL)

Bir hata oluştuğunda bot üzerinden şu şekilde bildirim alınır ve kontrol edilir:

```
🤖 AI SRE Manager — 25.06.2026 22:40

🚨 [BikeFit-API] Hata Tespit Edildi!
📍 Nerede: bikefit-api
💥 Ne oldu: ModuleNotFoundError: No module named 'slowapi'
🔧 Önerilen aksiyon: requirements.txt dosyasına 'slowapi' ekle

[ ✅ Kabul Et ]   [ ❌ Reddet ]
```

---

## Özellikler (Features)

| Özellik | Açıklama |
| :--- | :--- |
| **SQLite HITL State Store** | Onay bekleyen işlemleri kalıcı olarak tutar. Çökmelere ve restartlara karşı dayanıklıdır. |
| **6-Aşamalı LLM Pipeline** | Yerel MacBook modelinden başlayıp, ücretsiz bulut modellerine (Gemini, Groq) ve son çare olarak Claude'a uzanan en tasarruflu API zinciri. |
| **Atomic File Writes** | Kod değişiklikleri önce `.tmp` dosyasına yazılır, `py_compile` ve `import` testi ile doğrulanır, ardından atomik olarak değiştirilir. |
| **Bağımsız Watchdog** | Her 5 saniyede bir `.heartbeat` dosyasını kontrol eder. Sistem kilitlenirse Git rollback tetikler ve servisi yeniden başlatır. |
| **Güvenlik Sınırlandırması** | Sadece izin verilen komutlar (`docker compose`, `systemctl restart`) çalıştırılabilir; keyfi komutlar engellenir. |

---

## Kurulum (Setup)

### 1. Dosyaları kopyalayın ve bağımlılıkları yükleyin:

```bash
git clone https://github.com/kapucuonur/sre-daemon.git
cd sre-daemon
pip install -r requirements.txt
```

### 2. Ortam değişkenlerini yapılandırın (`.env`):

```env
# MacBook IP
MAC_IP=192.168.1.105

# API Anahtarları
GEMINI_API_KEY="AIzaSy..."
GROQ_API_KEY="gsk_..."
XAI_API_KEY="xai-..."
ANTHROPIC_API_KEY="sk-ant-..."

# Telegram Entegrasyonu
TELEGRAM_BOT_TOKEN="8477141465:AAEs..."
TELEGRAM_CHAT_ID="7491147357"
```

### 3. Servis olarak başlatın:

```bash
sudo cp sre-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sre-daemon
sudo systemctl start sre-daemon
```

---

## Lisans (License)

Proprietary — All Rights Reserved. You may not use, copy, or distribute this software without explicit written permission from TriHonor.

# 🤖 SRE Daemon

<p align="center">
  <img src="https://img.shields.io/badge/tests-19%20passed-4ade80?style=flat-square" alt="tests" />
  <img src="https://img.shields.io/badge/Raspberry%20Pi-5-C11A41.svg?logo=Raspberry%20Pi&style=flat-square" alt="Raspberry Pi" />
  <img src="https://img.shields.io/badge/Ollama-Local%20First-FC7E0F.svg?style=flat-square" alt="Ollama" />
  <img src="https://img.shields.io/badge/Groq-Llama%203.3-f55036.svg?style=flat-square" alt="Groq Llama 3.3" />
  <img src="https://img.shields.io/badge/Gemini-2.0%20Flash-4285F4.svg?logo=Google&style=flat-square" alt="Gemini 2.0" />
  <img src="https://img.shields.io/badge/Claude-Sonnet-d97706.svg?style=flat-square" alt="Claude Sonnet" />
  <img src="https://img.shields.io/badge/License-Proprietary-blue.svg?style=flat-square" alt="License" />
</p>

> An advanced, production-grade AI-powered self-healing SRE daemon for Raspberry Pi 5, VPS, and cloud VMs. It monitors systemd journals and Docker events in real-time, diagnoses errors using a multi-tier hierarchical LLM fallback stack, and executes safe auto-remediation with a Human-in-the-Loop (HITL) approval gateway and dynamic whitelist learning.

---

## ⚡ Quick Start & Installation

Depending on your subscription plan, install SRE Daemon using one of the following commands:

### Option A: Starter Tier (Self-Hosted Free)
Runs entirely locally using your own infrastructure and configuration:
```bash
curl -sSL https://sre-daemon.com/install.sh | bash
```

### Option B: Pro / Scale Tiers (Managed Dashboard)
Unlocks the hosted **SRE Platform Dashboard**, managed updates, and shared cloud LLM budgets:
```bash
curl -sSL https://sre-daemon.com/install.sh | SRE_API_KEY=sre_live_xxxxxxxxxxxxxxxx bash
```

---

## Key Concepts

* **AI SRE (Site Reliability Engineer)**: An autonomous software agent that monitors system logs and Docker containers 24/7 to analyze and repair infrastructure issues automatically.
* **Human-in-the-Loop (HITL)**: A safety gate pattern where the AI proposes a code patch or system change, but awaits explicit human confirmation (via interactive Telegram buttons or Slack actions) before executing it in production.
* **Stateless Retries**: A reliable failover pattern where each fallback model in the stack is initialized with the original, raw error log. If a model fails or halts, the next fallback starts with a clean slate, preventing the propagation of incorrect assumptions.
* **Dinamik Whitelist Öğrenme**: Sabit kurallarla sınırlı kalmayan, güvenlik kurallarına uygun whitelist onay sürecinin LLM katmanları tarafından otonom olarak regex pattern'lerine dönüştürülüp kalıcı olarak kaydedilmesi.

---

## Architecture & LLM Cascade Pipeline

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
      ├── 3. Dynamic Whitelist Learning Engine (learned_patterns.json)
      │
      └── 4. 6-Tier Hierarchical LLM Fallback Stack
            ├── 1. Local Pi Ollama (Fast)    --> qwen2.5-coder:7b (Offline / Free)
            ├── 2. Local Pi Ollama (Deep)    --> qwen2.5-coder:32b (Offline / Free)
            ├── 3. MacBook Ollama (Network)  --> qwen2.5-coder:32b (Heavy Local / Free)
            ├── 4. Groq Cloud API            --> llama-3.3-70b-versatile (Hızlı / Free)
            ├── 5. Google Gemini API         --> gemini-2.0-flash / 2.5-flash (Cloud / Free)
            └── 6. Anthropic Claude API      --> claude-sonnet-4-5 / 4-6 (Son Çare / Ücretli)
```

---

## How It Works

1. **Monitor**: `systemd journal` and Docker events are monitored in real-time.
2. **Detection**: Errors and container crashes are captured and filtered.
3. **Analysis**: The error is sent to the hierarchical LLM pipeline. The first active model analyzes the error, identifies the root cause, and generates a remediation action plan (`actions`).
4. **Strategy Registry (Otonom Hafıza)**:
   - Başarıyla çözülen hataların traceback imzası (SHA-256) ve çözüm komutları SQLite veritabanında saklanır.
   - Aynı hata tekrar oluştuğunda LLM zincirine hiç istek atılmadan doğrudan hafızadaki komut çalıştırılır ($0.0000 API maliyeti).
   - **Cross-Container Genelleme:** Hata loglarındaki container/servis isimleri temizlenerek aynı hata tiplerinin tüm altyapıda ortak çözülmesi sağlanır.
   - **Zaman Bazlı Çürüme (Weight Decay):** Çözümlerin başarı puanları zamanla çürütülür ($W_{decayed} = W_{base} \times 0.5^{(\text{age\_days}/30)}$). Başarısız olan çözümler otomatik olarak kara listeye alınır.
5. **Dinamik Whitelist Kontrolü**:
   - Güvenli komutlar (örn: `docker restart`) doğrudan whitelist ile eşleşerek çalışır.
   - Whitelist dışındaki komutlar LLM onay zincirine (`llm_approve_for_whitelist`) gönderilir. Güvenli bulunursa otomatik olarak yeni bir regex kuralı öğrenilir ve `learned_patterns.json` dosyasına eklenir.
   - Tehlikeli karakter filtresi (`|`, `;`, `$`, vb.) barındıran komutlar LLM'e gitmeden doğrudan engellenir.
6. **Approval & Remediation**: Kritik riskli komutlar SQLite veritabanına kaydedilerek Telegram ve Slack onay butonlarına sunulur. Onay sonrası atomik olarak test edilip uygulanır.
7. **Watchdog Protection**: Eğer daemon kilitlenirse bağımsız watchdog otomatik olarak `git rollback` tetikler ve servisi yeniden başlatır.

---

## 📊 Monitoring & Alerts (ai_log_analyst.py)

Sistem genelindeki logların durumunu periyodik olarak kontrol eden bağımsız bir log analizörüdür (`ai_log_analyst.py`).
- Her 30 dakikada bir çalışarak sistem log dosyalarını (`/home/pi/sre/daemon.log` vb.) ve Docker loglarını analiz eder.
- Olağandışı durumları saptayarak Telegram ve Slack üzerinden anında bildirim gönderir.

---

## Features

| Feature | Description |
| :--- | :--- |
| **SQLite HITL State Store** | Persistently tracks pending approvals, surviving crashes and service restarts. |
| **6-Tier LLM Pipeline** | Hierarchical fallback starting from ultra-fast local Ollama on Pi 5, scaling to Groq, Gemini and falling back to Claude Sonnet to minimize API costs. |
| **Strategy Registry** | Learns successful healing commands and replicates them instantly without LLM calls. Includes Weight Decay algorithm. |
| **Dynamic Whitelist Learning** | Self-learning execution security layer which generates regex patterns for approved commands dynamically. |
| **ChatOps Integration** | Full Telegram buttons and Slack interactive action handlers for quick remote infrastructure administration. |
| **Atomic File Writes** | Patches are validated via `py_compile` and import check, then replaced atomically. |
| **Independent Watchdog** | Monitors a `.heartbeat` file every 5s. Triggers `git rollback` and restarts the service if the system locks up. |

---

## Setup

### 1. Clone the repository and install dependencies:

```bash
git clone https://github.com/kapucuonur/sre-daemon.git
cd sre-daemon
pip install -r requirements.txt
```

### 2. Configure environment variables (`.env`):

```env
# MacBook IP (or any local client running Ollama on local network)
MAC_IP=192.168.x.x

# API Keys
GEMINI_API_KEY="your-gemini-api-key"
GROQ_API_KEY="your-groq-api-key"
XAI_API_KEY="your-xai-api-key"
ANTHROPIC_API_KEY="your-anthropic-api-key"

# Telegram Integration
TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
TELEGRAM_CHAT_ID="your-telegram-chat-id"

# Slack Integration
SLACK_BOT_TOKEN="xoxb-your-slack-token"
SLACK_CHANNEL_ID="C0XXXXXXXXX"
LITELLM_API_KEY="sk-1234"
```

### 3. Start as a systemd service:

```bash
sudo cp sre-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sre-daemon
sudo systemctl start sre-daemon
```

---

## License

Proprietary — All Rights Reserved. You may not use, copy, or distribute this software without explicit written permission from TriHonor.



https://github.com/user-attachments/assets/e32d2a76-ed9f-4a51-bcbe-3ee569194e79

# SRE Daemon — AI Self-Healing Server

> AI-powered self-healing SRE daemon for Raspberry Pi 5. Monitors systemd journals and Docker events in real-time, diagnoses errors with a hierarchical LLM stack (local → cloud), and executes auto-remediation — no human intervention needed.

---

## Architecture

```
Raspberry Pi 5
│
├── systemd journal (priority 0-3: emerg/alert/crit/err)
├── Docker events (die / oom / kill)
│
└── SRE Daemon ──► LLM Failover Chain
                    1. Mac (LiteLLM Proxy) → qwen2.5-coder:32b  [online]
                    2. Pi  (Ollama)        → qwen2.5-coder:7b   [Mac offline]
                    3. Anthropic           → claude-sonnet-4-6   [3 local failures]
```

---

## How It Works

<<<<<<< Updated upstream
1. **Monitor** — Systemd journal and Docker events are streamed in real-time
2. **Detect** — Errors are classified by severity (emerg/alert/crit/err)
3. **Diagnose** — Error sent to LLM failover chain; root cause identified
4. **Remediate** — Actions executed automatically:
   - `requirements.txt` patching
   - `docker compose up -d --build`
   - Service restart via systemctl
5. **Log** — All decisions and actions stored with timestamps

---

## Real-World Example

```
bikefit-api container crashed
  ERROR: ModuleNotFoundError: No module named 'slowapi'

→ SRE Daemon detected the crash via Docker die event
→ LLM identified missing dependency
→ Appended 'slowapi' to requirements.txt
→ Executed: docker compose up -d --build
→ Container recovered ✅  (elapsed: ~45s)
```

---

## Features

| Feature | Detail |
|---|---|
| 🔍 Real-time monitoring | systemd journal (priority 0–3) + Docker events |
| 🤖 Hierarchical LLM failover | Mac LiteLLM Proxy → Pi Ollama → Anthropic Claude |
| 🧠 LLM model | `claude-sonnet-4-6` (cloud fallback) / `qwen2.5-coder:32b` (local) |
| ⚡ Hailo-8 NPU | Hardware-accelerated inference on Raspberry Pi 5 AI Kit |
| 🔧 Auto-remediation | File patching, container rebuild, service restart |
| 📋 Audit log | Every action logged with timestamp and LLM reasoning |
| 🛡️ Safety checks | Allowlist-based command execution, no arbitrary shell |

---

## Stack

- **Hardware**: Raspberry Pi 5 + Hailo-8 NPU (AI Kit)
- **Local LLM**: Ollama — `qwen2.5-coder:32b` (Mac) / `qwen2.5-coder:7b` (Pi)
- **Cloud LLM**: Anthropic `claude-sonnet-4-6`
- **LLM Routing**: LiteLLM Proxy on Mac (routes Pi → Mac → Anthropic)
- **Monitoring**: systemd journal, Docker SDK
- **Language**: Python 3.11+

---

## Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/kapucuonur/sre-daemon.git
cd sre-daemon
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

**Required variables:**

```env
ANTHROPIC_API_KEY=sk-ant-...
MAC_OLLAMA_URL=http://192.168.x.x:11434   # Your Mac's IP
PI_OLLAMA_URL=http://localhost:11434
LITELLM_PROXY_URL=http://192.168.x.x:4000 # LiteLLM on Mac (optional)
```

### 3. Run

```bash
# Direct
python sre_daemon.py

# As systemd service (recommended)
sudo systemctl enable sre-daemon
sudo systemctl start sre-daemon
```

### 4. Monitor journal stream

```bash
bash journal_to_file.sh
```

---

## LiteLLM Proxy (Mac)

The daemon optionally routes through a LiteLLM proxy running on your Mac, enabling:
- Smart model routing (local → cloud fallback)
- Usage logging and cost tracking
- Single API endpoint for all models

```bash
# On Mac:
litellm --config litellm_config.yaml --port 4000
```

---

## Files

| File | Description |
|---|---|
| `sre_daemon.py` | Main daemon — monitoring, LLM integration, remediation engine |
| `journal_to_file.sh` | Streams systemd journal to file for daemon consumption |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |

---

## Roadmap

- [ ] Web dashboard (real-time incident feed)
- [ ] Slack / Telegram alert integration
- [ ] Multi-Pi cluster support
- [ ] Prometheus metrics export

---

## License

Proprietary — All Rights Reserved. You may not use, copy, or distribute this software without explicit written permission from TriHonor.
=======
1. **Systemd Journal** and **Docker Events** are monitored in real-time
2. When an error is detected, it is sent to an LLM (hierarchical fallback):
   - Mac online → Mac Ollama 
>>>>>>> Stashed changes

## Installation

```bash
git clone https://github.com/kapucuonur/sre-daemon.git
cd sre-daemon
pip install -r requirements.txt

cp .env.example .env
nano .env  # Fill in your values

sudo cp sre-daemon.service /etc/systemd/system/
sudo systemctl enable --now sre-daemon

systemctl status sre-daemon
```

## Requirements

- Linux server (Raspberry Pi 5 or any VPS)
- Python 3.10+
- Docker
- Ollama (optional, for local LLM)
- Anthropic API key (cloud fallback)

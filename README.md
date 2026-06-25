

https://github.com/user-attachments/assets/218de02b-4c89-4d2e-b764-8d0dbf694ddd

# SRE Daemon — AI Self-Healing Engine (v5.1)

> An advanced, production-grade AI-powered self-healing SRE daemon for Raspberry Pi 5. It monitors systemd journals and Docker events in real-time, diagnoses errors using a 6-tier hierarchical LLM fallback stack, and executes safe auto-remediation with a Human-in-the-Loop (HITL) approval gateway.

---

## Key Concepts

* **AI SRE (Site Reliability Engineer)**: An autonomous software agent that monitors system logs and Docker containers 24/7 to analyze and repair infrastructure issues automatically.
* **Human-in-the-Loop (HITL)**: A safety gate pattern where the AI proposes a code patch or system change, but awaits explicit human confirmation (in our case, via interactive Telegram buttons) before executing it in production.
* **Stateless Retries**: A reliable failover pattern where each fallback model in the stack is initialized with the original, raw error log. If a model fails or hallucinates, the next fallback starts with a clean slate, preventing the propagation of incorrect assumptions.

---

## Architecture

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

## How It Works

1. **Monitor**: `systemd journal` and Docker events are monitored in real-time.
2. **Detection**: Errors and container crashes are captured and filtered.
3. **Analysis**: The error is sent to a 6-stage hierarchical LLM pipeline. The first active model analyzes the error, identifies the root cause, and generates a remediation action plan (`actions`).
4. **Risk Assessment**:
   * **Low/Medium Risk**: Container restarts, `requirements.txt` updates, and similar minor operations are applied **automatically**.
   * **High/Critical Risk**: Code writing or modifications are saved in the SQLite database, triggering a **Telegram Approval Prompt**.
5. **Approval & Remediation**: Once the user approves or rejects via the `Approve` / `Reject` buttons on Telegram, the code is compiled, tested, and updated atomically.
6. **Watchdog Protection**: If the daemon freezes or enters a crash loop after a change, an independent watchdog process automatically triggers a `git rollback` and restarts the service.

---

## Sample Interface & Notifications (Telegram HITL)

When an error is detected, a notification is sent via the bot:

```
🤖 AI SRE Manager — 25.06.2026 22:40

🚨 [BikeFit-API] Error Detected!
📍 Service: bikefit-api
💥 Details: ModuleNotFoundError: No module named 'slowapi'
🔧 Proposed Action: Add 'slowapi' to requirements.txt

[ ✅ Approve ]   [ ❌ Reject ]
```

---

## Features

| Feature | Description |
| :--- | :--- |
| **SQLite HITL State Store** | Persistently tracks pending approvals, surviving crashes and service restarts. |
| **6-Tier LLM Pipeline** | Seamless failover starting from local MacBook LLM, to free cloud APIs (Gemini, Groq), and falling back to Claude as a last resort to minimize API costs. |
| **Atomic File Writes** | Patches are written to a `.tmp` file, validated via `py_compile` and import check, then replaced atomically. |
| **Independent Watchdog** | Monitors a `.heartbeat` file every 5s. Triggers `git rollback` and restarts the service if the system locks up. |
| **Security Sandbox** | Strict whitelist for allowed commands (`docker compose`, `systemctl restart`); arbitrary shell execution is blocked. |

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
# MacBook IP (or any local client running Ollama)
MAC_IP=192.168.x.x

# API Keys
GEMINI_API_KEY="your-gemini-api-key"
GROQ_API_KEY="your-groq-api-key"
XAI_API_KEY="your-xai-api-key"
ANTHROPIC_API_KEY="your-anthropic-api-key"

# Telegram Integration
TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
TELEGRAM_CHAT_ID="your-telegram-chat-id"
```

### 3. Start as a systemd service:

```bash
sudo cp sre-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sre-daemon
sudo systemctl start sre-daemon
```

---

## Future Roadmap & Vision (v6.0 Planning)

We are evolving `sre-daemon` from a simple healing agent into a fully-featured **SRE ChatOps Platform**. Our goal is to balance full automation with surgical human control while keeping the Raspberry Pi 5 resource footprint near zero.

### 🧠 v6.0 Memory & Rule Engine
* **[Planned] Local Memory Cache (SQLite + difflib)**:
  * Store a structured hash/signature of the traceback and its successful repair script in `sre_state.db`.
  * Before calling any LLM, perform a local fuzzy search using Python's `difflib.SequenceMatcher` to find matching historical errors.
  * If a successful fix is found, propose it directly to the user, bypassing LLM API latency and cost entirely.
* **[Planned] Auto-Apply Safe Rules**:
  * Implement a counter for repeatedly approved fixes.
  * If a specific repair script is manually approved 5 times with 100% success rate, the daemon elevates it to "Auto-Apply", resolving the crash immediately without sending a Telegram prompt.

### 📱 ChatOps & Visual Monitoring
* **[Planned] Telegram Log Insight**:
  * Dynamically integrate the last 15-20 lines of the crash log directly inside the Telegram alert card.
* **[Planned] Interactive Human-in-the-Loop Patching**:
  * Advanced approval workflow allowing the user to reply to the Telegram bot with modifications to the proposed patch before clicking Approve.
* **[Planned] Telegram-Native Visual Reports (`/stats`)**:
  * To avoid hosting a heavy, resource-consuming web dashboard on the Pi 5, we will generate statistics charts using `matplotlib` locally and serve them directly as images on Telegram via a `/stats` command (e.g. daily/weekly SRE reports, cost savings, uptime metrics).


---

## License

Proprietary — All Rights Reserved. You may not use, copy, or distribute this software without explicit written permission from TriHonor.


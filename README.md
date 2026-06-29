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
curl -sSL https://sre.trihonor.com/install.sh | bash
```

### Option B: Pro / Scale Tiers (Managed Dashboard)
Unlocks the hosted **SRE Platform Dashboard**, managed updates, and shared cloud LLM budgets:
```bash
curl -sSL https://sre.trihonor.com/install.sh | SRE_API_KEY=sre_live_xxxxxxxxxxxxxxxx bash
```

---

## Key Concepts

* **AI SRE (Site Reliability Engineer)**: An autonomous software agent that monitors system logs and Docker containers 24/7 to analyze and repair infrastructure issues automatically.
* **Human-in-the-Loop (HITL)**: A safety gate pattern where the AI proposes a code patch or system change, but awaits explicit human confirmation (via interactive Telegram buttons or Slack actions) before executing it in production.
* **Stateless Retries**: A reliable failover pattern where each fallback model in the stack is initialized with the original, raw error log. If a model fails or halts, the next fallback starts with a clean slate, preventing the propagation of incorrect assumptions.
* **Dynamic Whitelist Learning**: A self-learning execution security layer that automatically translates validated, non-malicious command exceptions into regex rules via LLMs and persists them to `learned_patterns.json`.

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
      └── 4. 5-Tier Hierarchical LLM Fallback Stack
            ├── 1. MacBook Ollama (Network) --> qwen2.5-coder:32b (Heavy Local / Free)
            ├── 2. Local Pi Ollama (Fast)    --> qwen2.5-coder:7b (Offline Fallback / Free)
            ├── 3. Groq Cloud API            --> llama-3.3-70b-versatile (Fast / Free)
            ├── 4. Google Gemini API         --> gemini-2.0-flash / 2.5-flash (Cloud / Free)
            └── 5. Anthropic Claude API      --> claude-sonnet-4-6 (Last Resort / Expensive)
```

---

## How It Works

1. **Monitor**: `systemd journal` and Docker events are monitored in real-time.
2. **Detection**: Errors and container crashes are captured and filtered.
3. **Analysis**: The error is sent to the hierarchical LLM pipeline. The first active model analyzes the error, identifies the root cause, and generates a remediation action plan (`actions`).
4. **Strategy Registry (Autonomous Memory)**:
   - Traceback signatures (SHA-256 hashes) and their successful resolution commands are saved in `sre_state.db`.
   - When the same incident recurs, the daemon skips the LLM stack entirely and executes the cached fix directly ($0.0000 API cost).
   - **Cross-Container Generalization:** Sanitizes container/service names and syslogs so similar failures across different containers share the same cached strategies.
   - **Weight Decay:** Over time, the reliability weight of a cached strategy is decayed (W_decayed = W_base × 0.5^(age_days/30)). If a cached strategy fails, its weight is reduced, and it gets automatically blacklisted if the weight falls below zero.
5. **Dynamic Whitelist Filter**:
   - Safe commands (e.g. `docker restart`) run immediately via predefined regex matches.
   - Unrecognized commands are piped through the `llm_approve_for_whitelist` chain. If approved as safe, a minimal regex pattern is generated and persisted to `learned_patterns.json`.
   - Commands containing dangerous characters (`|`, `;`, `$`, etc.) are blocked immediately without query to the LLM.
6. **Approval & Remediation**: Critical/high-risk commands are saved to the SQLite state store and forwarded as interactive block actions to Slack and Telegram. Once approved, the patch is atomically verified and applied.
7. **Watchdog Protection**: If the daemon locks up, the independent watchdog triggers a `git rollback` and restarts the service.

---

## 📊 Monitoring & Alerts (ai_log_analyst.py)

An independent background log monitor (`ai_log_analyst.py`) runs periodically via cron.
- Analyzes system logs (`/home/pi/sre/daemon.log` etc.) and Docker logs over the last 30 minutes.
- Detects unusual patterns or errors and dispatches notifications via Slack and Telegram.

---

## Features

| Feature | Description |
| :--- | :--- |
| **SQLite HITL State Store** | Persistently tracks pending approvals, surviving crashes and service restarts. |
| **5-Tier LLM Pipeline** | Hierarchical fallback starting from heavy local Ollama on Mac, scaling to local Pi, Groq, Gemini and falling back to Claude Sonnet to minimize API costs. |
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

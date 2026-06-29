# 🤖 SRE Daemon

<p align="center">
  <img src="https://img.shields.io/badge/tests-31%20passed-4ade80?style=flat-square" alt="tests" />
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20VPS%20%7C%20Pi%205-6366f1?style=flat-square" alt="Platform" />
  <img src="https://img.shields.io/badge/Ollama-Local%20First-FC7E0F.svg?style=flat-square" alt="Ollama" />
  <img src="https://img.shields.io/badge/Groq-Llama%203.3-f55036.svg?style=flat-square" alt="Groq Llama 3.3" />
  <img src="https://img.shields.io/badge/Gemini-2.5%20Flash-4285F4.svg?logo=Google&style=flat-square" alt="Gemini 2.5" />
  <img src="https://img.shields.io/badge/Claude-Sonnet-d97706.svg?style=flat-square" alt="Claude Sonnet" />
  <img src="https://img.shields.io/badge/License-Proprietary-blue.svg?style=flat-square" alt="License" />
</p>

<p align="center">
  <img src="docs/demo_simulation_active.png" alt="SRE Daemon Demo" width="800" />
</p>

> A production-grade, **platform-agnostic** AI-powered self-healing daemon for any Linux server — Raspberry Pi 5, Ubuntu VPS, Debian, RHEL, Docker Swarm, and more. Monitors systemd journals and Docker events in real-time, diagnoses errors using a multi-tier LLM cascade, and autonomously repairs infrastructure through safe code patching, dynamic service rebuilds, and Human-in-the-Loop approvals.

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

> `install.sh` automatically discovers your running Docker containers and generates a `manifest.yaml` — no manual configuration needed.

---

## Key Concepts

* **Cognitive SRE (Predictive Maintenance)**: Moving beyond reactive log-healing, SRE Daemon continuously analyzes system and container metrics (CPU/RAM/Disk), detects exponential growth trends via Exponential Moving Averages (EMA), and executes pre-emptive restarts or optimizations before outages occur.
* **Human-in-the-Loop (HITL)**: A safety gate where the AI proposes a code patch, but awaits explicit human confirmation (via Telegram buttons or Slack actions) before executing it in production.
* **Strategy Registry (Fuzzy Semantic Matching)**: Traceback signatures and successful commands are indexed in `sre_state.db`. If an exact hash miss occurs, a fuzzy semantic match (`difflib` > 82%) applies existing strategies across different containers, achieving **0-token cost** for similar problems.
* **Language-Agnostic Validation**: Pre-validates patches in a temporary sandbox using appropriate compiler checks (`py_compile`, `node --check`, `ruby -c`, `bash -n`, `php -l`) before committing changes.
* **Service Auto-Discovery**: Uses `docker inspect` at runtime to resolve container-internal paths (e.g. `/app/main.py`) to physical host paths — no hardcoded directories.

---

## Architecture & LLM Cascade Pipeline

```
Any Linux Server (Pi 5, Ubuntu VPS, Debian, RHEL, ...)
│
├── systemd journal (priority 0-3: emerg/alert/crit/err)
├── Docker events (die / oom / kill)
├── Metrics Collector (psutil & docker stats time-series)
│
└── SRE Daemon (sre_daemon.py)
      │
      ├── ManifestLoader      → reads manifest.yaml (services, runtimes, limits)
      ├── ServiceDiscovery    → docker inspect → resolves container paths dynamically
      ├── LanguageValidator   → .py .js .go .rb .sh .php syntax sandbox
      ├── RebuildManager      → docker-compose / systemd / PM2 / Kubernetes / bare
      ├── MetricsCollector    → SQLite time-series (stats_history.db) & EMA Anomaly Engine
      │
      ├── SQLite HITL State Machine (sre_state.db)
      ├── Independent Watchdog & Heartbeat
      ├── Dynamic Whitelist Learning Engine (learned_patterns.json)
      │
      └── 5-Tier Hierarchical LLM Fallback Stack
            ├── 1. MacBook Ollama (Network) --> qwen2.5-coder:32b (Heavy Local / Free)
            ├── 2. Local Ollama (Fast)       --> qwen2.5-coder:7b  (Offline / Free)
            ├── 3. Groq Cloud API            --> llama-3.3-70b-versatile (Fast / Free)
            ├── 4. Google Gemini API         --> gemini-2.5-flash (Cloud / Free)
            └── 5. Anthropic Claude API      --> claude-sonnet-4-6 (Last Resort / Expensive)
```

---

## How It Works

1. **Monitor & Collect**: `systemd journal` and Docker events are monitored in real-time while container/host metrics are recorded in `stats_history.db` every 30 seconds (capped to 24h).
2. **Predictive Anomaly Detection**: Calculates a 20-point EMA. If usage spikes above the manifest limits and exceeds 1.5x of the EMA, SRE Daemon fires a Telegram pre-emptive warning and restarts the container 10 seconds before a crash occurs.
3. **Diagnostics (Layer 2)**:
   - Python/Node/Go stack tracebacks are parsed automatically.
   - `ServiceDiscovery` calls `docker inspect` to translate container paths to real host paths.
   - A 50-line surrounding code context is extracted and injected into the LLM prompt.
   - SRE Persona constraints enforce safe, relative-path, minimal surgical patches.
4. **Strategy Registry**:
   - Exact hash matches and fuzzy semantic hits (>82% match) reuse historical success commands without query costs.
   - **Weight Decay:** Base weights decay over time ($W_{decayed} = W \times 0.5^{age/30}$). Repetitive command failures automatically blacklist strategy records.
5. **Dynamic Whitelist Filter**:
   - Unrecognized commands trigger `llm_approve_for_whitelist`. Whitelisted patterns are saved to `learned_patterns.json`.
6. **HITL & Visual Diffs (Layer 3)**:
   - Visual unified diffs (`- old` / `+ new`) are sent to Telegram/Slack. Sandboxed syntax validation runs on target files. Atomic writes occur only on success, otherwise rolled back from `.bak`.
7. **Auto-Rebuild**: After a successful patch, `RebuildManager` automatically triggers service restarts (docker-compose, systemd, PM2, Kubernetes, or bare shell).
8. **Budget & Limits**: Background thread resets cloud LLM budget counters at midnight UTC.
9. **Watchdog Protection**: Monitors `.heartbeat` every 5s. Trigger git rollback on freeze.

---

## manifest.yaml — Service Configuration

`install.sh` auto-generates `manifest.yaml` by discovering running Docker containers. Edit it to configure systemd units, PM2 apps, Kubernetes deployments, and custom metrics thresholds:

```yaml
# SRE Daemon — Tenant Manifest
# Auto-generated by install.sh — edit as needed

tenant_id: "cus_abc123"      # SRE Platform tenant ID (managed plan)
sre_api_key: ""               # Leave blank for free self-hosted

services:
  - name: "my-api"
    runtime: "docker-compose"
    compose_file: "/home/user/myapp/docker-compose.yml"
    container_name: "myapp-api"
    limits:
      cpu_threshold: 80.0       # Trigger warning/restart if CPU > 80%
      mem_threshold: 85.0       # Trigger warning/restart if RAM > 85%
      anomaly_sensitivity: 1.5  # Trigger if current metric > 1.5x EMA

  - name: "background-worker"
    runtime: "systemd"
    unit: "myworker.service"

  - name: "frontend"
    runtime: "pm2"
    app_name: "my-frontend"

  - name: "k8s-api"
    runtime: "kubernetes"
    namespace: "production"
    deployment: "api-deployment"
```

**Supported runtimes:** `docker-compose` · `systemd` · `pm2` · `kubernetes` · `bare`

---

## 📊 Monitoring & Alerts (ai_log_analyst.py)

An independent background log monitor (`ai_log_analyst.py`) runs periodically via cron.
- Analyzes system logs and Docker logs over the last 30 minutes.
- Detects unusual patterns or errors and dispatches notifications via Slack and Telegram.

---

## Features

| Feature | Description |
| :--- | :--- |
| **Service Auto-Discovery** | Uses `docker inspect` at runtime to resolve container paths to host paths. Zero hardcoded directories. |
| **Multi-Runtime Rebuild** | After a successful patch, automatically rebuilds the affected service: docker-compose, systemd, PM2, Kubernetes, or bare process. |
| **Language-Agnostic Validator** | Sandboxes patches with the correct tool per language: `.py` → `py_compile`, `.js` → `node --check`, `.rb` → `ruby -c`, `.sh` → `bash -n`, and more. |
| **Visual Git Diffs** | Computes and displays interactive code changes as unified diff in Telegram notifications. |
| **5-Tier LLM Pipeline** | Hierarchical fallback from local Ollama → Groq → Gemini → Claude to minimize API costs. |
| **Strategy Registry** | Learns successful healing commands and replicates them instantly without LLM calls. Weight Decay algorithm included. |
| **Dynamic Whitelist Learning** | Self-learning execution security layer that generates regex patterns for approved commands. |
| **ChatOps Integration** | Full Telegram buttons and Slack interactive action handlers for remote infrastructure administration. |
| **SQLite HITL State Store** | Persistently tracks pending approvals, surviving crashes and service restarts. |
| **Budget Auto-Reset** | Daily API usage counters reset automatically at midnight UTC. |
| **Independent Watchdog** | Monitors `.heartbeat` every 5s. Triggers `git rollback` and restarts the service if the daemon locks up. |

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
# Optional: MacBook IP running Ollama on local network
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
```

### 3. Configure services in `manifest.yaml`:

```bash
# Auto-generate from running containers (recommended):
curl -sSL https://sre.trihonor.com/install.sh | bash

# Or manually edit after cloning:
nano manifest.yaml
```

### 4. Start as a systemd service:

```bash
sudo cp sre-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sre-daemon
sudo systemctl start sre-daemon
```

---

## License

Proprietary — All Rights Reserved. You may not use, copy, or distribute this software without explicit written permission from TriHonor.

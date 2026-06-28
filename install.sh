#!/usr/bin/env bash
# ============================================================
# SRE Daemon — AI Self-Healing Engine Installer
# Target OS: Linux (Ubuntu, Debian, Raspberry Pi OS)
# Usage: curl -sSL https://sre-daemon.com/install.sh | bash
# ============================================================

set -euo pipefail

INSTALL_DIR="${HOME}/sre"
SERVICE_NAME="sre-daemon"
PLATFORM_DEFAULT_URL="https://sre-api.trihonor.com"

echo "========================================================"
echo "      🤖 SRE Daemon Installer — AI Infrastructure       "
echo "========================================================"
echo ""

# 1. Check prerequisites
echo "→ Checking system requirements..."
for cmd in git python3 pip3 curl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "❌ Error: '$cmd' is not installed. Please install it first."
        exit 1
    fi
done

# 2. Check for python3-venv
if ! python3 -c "import venv" &>/dev/null; then
    echo "→ Installing python3-venv dependency..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -y && sudo apt-get install -y python3-venv
    else
        echo "❌ Error: python3-venv is missing. Please install it using your package manager."
        exit 1
    fi
fi

# 3. Create install directory
echo "→ Creating target directory: $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# 4. Clone or update repository
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "→ Updating existing repository in $INSTALL_DIR..."
    cd "$INSTALL_DIR"
    git fetch origin
    git reset --hard origin/main
else
    echo "→ Cloning SRE Daemon repository..."
    git clone https://github.com/kapucuonur/sre-daemon.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 5. Virtual Environment setup
echo "→ Creating Python virtual environment..."
python3 -m venv venv
echo "→ Installing dependencies in virtual environment..."
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt --quiet

# 6. Configure environment variables (.env)
echo "→ Configuring SRE Daemon environment settings..."
ENV_FILE="$INSTALL_DIR/.env"

# API Key prompt
API_KEY="${SRE_API_KEY:-}"
PLATFORM_URL="${SRE_PLATFORM_URL:-$PLATFORM_DEFAULT_URL}"

if [ -z "$API_KEY" ]; then
    echo ""
    read -r -p "🔑 Enter your SRE API Key (Press Enter to bypass for Free Self-Host): " USER_KEY
    API_KEY="$USER_KEY"
fi

# Write .env file
cat <<EOT > "$ENV_FILE"
# SRE Daemon Environment Settings
INSTALL_DIR=$INSTALL_DIR
HEAL_LOG=$INSTALL_DIR/heal_log.jsonl
PI_OLLAMA_URL=http://localhost:11434
EOT

if [ -n "$API_KEY" ]; then
    cat <<EOT >> "$ENV_FILE"
SRE_API_KEY=$API_KEY
SRE_PLATFORM_URL=$PLATFORM_URL
EOT
    echo "✓ Managed plan enabled with API key: ${API_KEY:0:12}..."
else
    echo "✓ Starter tier enabled (Free Self-Host mode)."
fi

chmod 600 "$ENV_FILE"

# 7. Create and register systemd service
echo "→ Registering systemd daemon service..."
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOT
[Unit]
Description=Self-Healing SRE AI Daemon
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/sre_daemon.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOT

echo "→ Loading and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "========================================================"
echo "    ✅ SRE Daemon Installation Completed Successfully!  "
echo "========================================================"
echo "Uptime:      systemctl status $SERVICE_NAME"
echo "Live Logs:   journalctl -u $SERVICE_NAME -f"
echo "Audits:      tail -f $INSTALL_DIR/heal_log.jsonl"
echo "========================================================"

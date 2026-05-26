#!/usr/bin/env bash
# deploy.sh — Master Deployment Script
# One-click install of the golden image system on any machine
# Usage: curl -fsSL https://your-domain.com/deploy.sh | bash
#    or: ./deploy.sh [--hub] [--tier N] [--model MODEL]

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

REPO_URL="https://raw.githubusercontent.com/targeted-design-agency/golden-image/main"
VERSION="1.0.0"
INSTALL_DIR="/opt/golden-image"
CONFIG_DIR="/etc/golden-image"
LOG="/var/log/golden-image-deploy.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── Functions ────────────────────────────────────────────────────────────────

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*" | tee -a "$LOG"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "$LOG"; }
error() { echo -e "${RED}[ERROR]${NC} $*" | tee -a "$LOG"; exit 1; }
info() { echo -e "${BLUE}[INFO]${NC} $*" | tee -a "$LOG"; }

usage() {
    cat <<EOF
Golden Image Deployment Script v${VERSION}

Usage: $0 [OPTIONS]

Options:
  --hub           Configure as hub (always-on server)
  --tier N        Force hardware tier (1-4)
  --model MODEL   Force specific Ollama model
  --offline       Skip model downloads (configure only)
  --uninstall     Remove golden image system
  --status        Show current status
  --help          Show this help

Examples:
  $0                    # Auto-detect and install
  $0 --hub              # Install as network hub
  $0 --tier 2           # Force Standard tier
  $0 --model qwen2.5:7b # Force specific model
EOF
    exit 0
}

# ── Parse Args ───────────────────────────────────────────────────────────────

HUB_MODE=false
FORCE_TIER=""
FORCE_MODEL=""
OFFLINE=false
UNINSTALL=false
STATUS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --hub) HUB_MODE=true; shift ;;
        --tier) FORCE_TIER="$2"; shift 2 ;;
        --model) FORCE_MODEL="$2"; shift 2 ;;
        --offline) OFFLINE=true; shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        --status) STATUS=true; shift ;;
        --help) usage ;;
        *) error "Unknown option: $1. Use --help for usage." ;;
    esac
done

# ── Status Check ─────────────────────────────────────────────────────────────

if [ "$STATUS" = true ]; then
    echo "Golden Image System Status"
    echo "=========================="
    echo "Version: $VERSION"
    echo "Install dir: $INSTALL_DIR"
    echo "Config dir: $CONFIG_DIR"
    echo ""
    if [ -f "$CONFIG_DIR/config.json" ]; then
        cat "$CONFIG_DIR/config.json" | python3 -m json.tool 2>/dev/null || cat "$CONFIG_DIR/config.json"
    else
        echo "Not configured yet. Run without --status to set up."
    fi
    if command -v ollama &>/dev/null; then
        echo ""
        echo "Ollama models:"
        ollama list 2>/dev/null || echo "  (Ollama not running)"
    fi
    exit 0
fi

# ── Uninstall ────────────────────────────────────────────────────────────────

if [ "$UNINSTALL" = true ]; then
    log "Uninstalling golden image system..."
    sudo systemctl stop ollama 2>/dev/null || true
    sudo systemctl disable ollama 2>/dev/null || true
    sudo rm -rf "$INSTALL_DIR" "$CONFIG_DIR"
    sudo rm -f /usr/local/bin/golden-image
    log "✓ Uninstalled. Ollama models preserved in ~/.ollama/"
    exit 0
fi

# ── Pre-flight Checks ────────────────────────────────────────────────────────

log "Starting Golden Image deployment v${VERSION}..."

# Check OS
if [ ! -f /etc/os-release ]; then
    error "Cannot detect OS. /etc/os-release not found."
fi
source /etc/os-release
info "OS: ${PRETTY_NAME:-$ID $VERSION_ID}"

# Check architecture
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ]; then
    error "Unsupported architecture: $ARCH. Only x86_64 is supported."
fi
info "Architecture: $ARCH"

# Check root/sudo
if [ "$EUID" -ne 0 ]; then
    if ! sudo -n true 2>/dev/null; then
        warn "This script requires sudo privileges."
        sudo -v || error "Failed to get sudo access."
    fi
fi

# ── Install Dependencies ─────────────────────────────────────────────────────

log "Installing dependencies..."

sudo apt-get update -qq 2>&1 | tail -3

PACKAGES="curl wget unzip python3 python3-pip git build-essential"
for pkg in $PACKAGES; do
    if ! dpkg -l "$pkg" &>/dev/null; then
        sudo apt-get install -y -qq "$pkg" 2>&1 | tail -2
    fi
done

log "✓ Dependencies installed"

# ── Create Directories ───────────────────────────────────────────────────────

sudo mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"
sudo chown "$(whoami):$(whoami)" "$INSTALL_DIR" "$CONFIG_DIR"

# ── Run Hardware Detection ───────────────────────────────────────────────────

log "Running hardware detection..."

if [ -n "$FORCE_TIER" ]; then
    TIER="$FORCE_TIER"
    log "→ Tier forced to: $TIER"
else
    TOTAL_RAM_GB=$(free -g | awk '/^Mem:/{print $2}')
    GPU_VRAM_MB=0
    if command -v nvidia-smi &>/dev/null; then
        GPU_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
    fi

    if [ "$TOTAL_RAM_GB" -ge 32 ]; then
        TIER=4
    elif [ "$TOTAL_RAM_GB" -ge 16 ]; then
        TIER=3
    elif [ "$TOTAL_RAM_GB" -ge 8 ]; then
        TIER=2
    else
        TIER=1
    fi
    log "→ Auto-detected tier: $TIER (${TOTAL_RAM_GB}GB RAM, ${GPU_VRAM_MB}MB VRAM)"
fi

# ── Select Model ─────────────────────────────────────────────────────────────

if [ -n "$FORCE_MODEL" ]; then
    OLLAMA_MODEL="$FORCE_MODEL"
    log "→ Model forced to: $OLLAMA_MODEL"
else
    case $TIER in
        1) OLLAMA_MODEL="tinyllama:1.1b" ;;
        2) OLLAMA_MODEL="qwen2.5:7b-instruct-q4_K_M" ;;
        3) OLLAMA_MODEL="qwen2.5:14b-instruct-q4_K_M" ;;
        4) OLLAMA_MODEL="qwen2.5:7b-instruct-q4_K_M" ;;
    esac
    log "→ Selected model: $OLLAMA_MODEL"
fi

# ── Install Ollama ───────────────────────────────────────────────────────────

if ! command -v ollama &>/dev/null; then
    log "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -5
    log "✓ Ollama installed"
else
    log "✓ Ollama already installed ($(ollama --version 2>/dev/null || echo 'version unknown'))"
fi

# Start Ollama
sudo systemctl enable --now ollama 2>&1 | tail -3

# Wait for Ollama
log "Waiting for Ollama to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        break
    fi
    sleep 2
done

# Pull model (unless offline)
if [ "$OFFLINE" = false ]; then
    log "Pulling model: $OLLAMA_MODEL..."
    ollama pull "$OLLAMA_MODEL" 2>&1 | tail -5
    log "✓ Model ready"
else
    log "→ Skipping model download (offline mode)"
fi

# ── Install Pinokio ──────────────────────────────────────────────────────────

PINOKIO_DIR="/opt/pinokio"
if [ ! -d "$PINOKIO_DIR" ]; then
    log "Installing Pinokio..."
    PINOKIO_URL="https://github.com/pinokiocomputer/pinokio/releases/latest/download/pinokio-linux-x64.zip"
    mkdir -p "$PINOKIO_DIR"
    curl -fsSL "$PINOKIO_URL" -o /tmp/pinokio.zip 2>&1 | tail -3
    unzip -q /tmp/pinokio.zip -d "$PINOKIO_DIR"
    rm -f /tmp/pinokio.zip
    log "✓ Pinokio installed"
else
    log "✓ Pinokio already installed"
fi

# ── Install Shared MCP Bridge ────────────────────────────────────────────────

BRIDGE_DIR="/opt/shared-bridge"
if [ ! -d "$BRIDGE_DIR" ]; then
    log "Installing shared MCP bridge..."
    mkdir -p "$BRIDGE_DIR"
    # Download from repo or copy from local
    if [ -f "$(dirname "$0")/server.py" ]; then
        cp "$(dirname "$0")/server.py" "$BRIDGE_DIR/"
        cp "$(dirname "$0")/run.sh" "$BRIDGE_DIR/" 2>/dev/null || true
    else
        curl -fsSL "$REPO_URL/server.py" -o "$BRIDGE_DIR/server.py" 2>&1 | tail -3
    fi
    log "✓ Shared MCP bridge installed"
else
    log "✓ Shared MCP bridge already installed"
fi

# ── Write Configuration ──────────────────────────────────────────────────────

log "Writing configuration..."

DEVICE_ID="$(hostname)-$(cat /etc/machine-id 2>/dev/null | cut -c1-8)"
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "unknown")

cat > "$CONFIG_DIR/config.json" <<EOF
{
  "version": "$VERSION",
  "tier": $TIER,
  "model": "$OLLAMA_MODEL",
  "mode": "$([ "$HUB_MODE" = true ] && echo "hub" || echo "spoke")",
  "device_id": "$DEVICE_ID",
  "local_ip": "$LOCAL_IP",
  "ollama_url": "http://localhost:11434",
  "mcp_port": 3000,
  "pinokio_dir": "$PINOKIO_DIR",
  "bridge_dir": "$BRIDGE_DIR",
  "installed_at": "$(date -Iseconds)"
}
EOF

log "✓ Configuration written to $CONFIG_DIR/config.json"

# ── Create CLI Command ───────────────────────────────────────────────────────

sudo tee /usr/local/bin/golden-image > /dev/null <<'CLI'
#!/usr/bin/env bash
# golden-image CLI
CONFIG_DIR="/etc/golden-image"
case "${1:-status}" in
    status)
        if [ -f "$CONFIG_DIR/config.json" ]; then
            cat "$CONFIG_DIR/config.json" | python3 -m json.tool 2>/dev/null
        else
            echo "Golden image not configured. Run deploy.sh first."
        fi
        ;;
    models)
        ollama list 2>/dev/null || echo "Ollama not running"
        ;;
    pull)
        ollama pull "${2:-qwen2.5:7b}" 2>&1 | tail -5
        ;;
    run)
        ollama run "${2:-qwen2.5:7b}"
        ;;
    stop)
        sudo systemctl stop ollama; echo "Ollama stopped"
        ;;
    start)
        sudo systemctl start ollama; echo "Ollama started"
        ;;
    restart)
        sudo systemctl restart ollama; echo "Ollama restarted"
        ;;
    *)
        echo "Usage: golden-image {status|models|pull|run|stop|start|restart}"
        ;;
esac
CLI
sudo chmod +x /usr/local/bin/golden-image

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Golden Image Deployment Complete!"
echo "=========================================="
echo ""
echo "  Device:     $DEVICE_ID"
echo "  Tier:       $TIER"
echo "  Model:      $OLLAMA_MODEL"
echo "  Mode:       $([ "$HUB_MODE" = true ] && echo "HUB" || echo "Spoke")"
echo "  IP:         $LOCAL_IP"
echo ""
echo "  Commands:"
echo "    golden-image status    — Show system status"
echo "    golden-image models    — List installed models"
echo "    golden-image pull MODEL — Download a model"
echo "    golden-image run MODEL — Chat with a model"
echo "    golden-image stop/start/restart — Control Ollama"
echo ""
echo "  Log: $LOG"
echo "=========================================="

#!/usr/bin/env bash
# golden-image-setup.sh — Hardware Detection & Auto-Configuration
# Run once on first boot of the golden image USB
# Detects hardware, selects model tier, configures everything

set -euo pipefail

LOG="/var/log/golden-image-setup.log"
exec > >(tee -a "$LOG") 2>&1

echo "=========================================="
echo "  Golden Image Setup — $(date)"
echo "=========================================="

# ── 1. Detect Hardware ──────────────────────────────────────────────────────

echo ""
echo "[1/6] Detecting hardware..."

# RAM
TOTAL_RAM_GB=$(free -g | awk '/^Mem:/{print $2}')
AVAILABLE_RAM_GB=$(free -g | awk '/^Mem:/{print $7}')
echo "  Total RAM: ${TOTAL_RAM_GB}GB"
echo "  Available RAM: ${AVAILABLE_RAM_GB}GB"

# CPU
CPU_CORES=$(nproc)
CPU_MODEL=$(grep "model name" /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)
echo "  CPU: $CPU_MODEL ($CPU_CORES cores)"

# GPU
GPU_INFO="none"
GPU_VRAM_MB=0
if command -v lspci &>/dev/null; then
    GPU_INFO=$(lspci 2>/dev/null | grep -i "vga\|3d\|display" | head -1 | cut -d: -f3 | xargs || echo "none")
fi
echo "  GPU: $GPU_INFO"

# Try to detect VRAM (NVIDIA)
if command -v nvidia-smi &>/dev/null; then
    GPU_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
    GPU_INFO="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo $GPU_INFO)"
    echo "  GPU VRAM: ${GPU_VRAM_MB}MB (NVIDIA)"
elif [ -f /sys/class/drm/card0/device/mem_info_vram_total ]; then
    GPU_VRAM_MB=$(( $(cat /sys/class/drm/card0/device/mem_info_vram_total 2>/dev/null || echo 0) / 1048576 ))
    echo "  GPU VRAM: ${GPU_VRAM_MB}MB (AMD/Intel)"
fi

# Storage
TOTAL_DISK_GB=$(df -BG / | awk 'NR==2{print $2}' | tr -d 'G')
FREE_DISK_GB=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
echo "  Disk: ${TOTAL_DISK_GB}GB total, ${FREE_DISK_GB}GB free"

# Network
HAS_NETWORK=false
HUB_IP=""
if command -v nmcli &>/dev/null; then
    ACTIVE_CONN=$(nmcli -t -f DEVICE,STATE dev 2>/dev/null | grep "connected" | head -1 | cut -d: -f1)
    if [ -n "$ACTIVE_CONN" ]; then
        HAS_NETWORK=true
        LOCAL_IP=$(nmcli -t -f IP4.ADDRESS dev show "$ACTIVE_CONN" 2>/dev/null | head -1 | cut -d/ -f1)
        echo "  Network: connected ($ACTIVE_CONN) — IP: $LOCAL_IP"
    fi
fi
if [ "$HAS_NETWORK" = false ]; then
    echo "  Network: not connected"
fi

# ── 2. Determine Tier ───────────────────────────────────────────────────────

echo ""
echo "[2/6] Determining hardware tier..."

# Scoring
TIER=1
TIER_NAME="Minimal"

# RAM scoring
if [ "$TOTAL_RAM_GB" -ge 32 ]; then
    TIER=4
    TIER_NAME="Hub"
elif [ "$TOTAL_RAM_GB" -ge 16 ]; then
    TIER=3
    TIER_NAME="Performance"
elif [ "$TOTAL_RAM_GB" -ge 8 ]; then
    TIER=2
    TIER_NAME="Standard"
fi

# GPU scoring (can bump up a tier)
if [ "$GPU_VRAM_MB" -ge 8000 ] && [ "$TIER" -lt 3 ]; then
    TIER=3
    TIER_NAME="Performance (GPU-boosted)"
elif [ "$GPU_VRAM_MB" -ge 4000 ] && [ "$TIER" -lt 2 ]; then
    TIER=2
    TIER_NAME="Standard (GPU-boosted)"
fi

echo "  → Tier $TIER: $TIER_NAME"

# ── 3. Select Model ─────────────────────────────────────────────────────────

echo ""
echo "[3/6] Selecting model for tier $TIER..."

case $TIER in
    1)
        OLLAMA_MODEL="tinyllama:1.1b"
        OLLAMA_MODEL_SIZE="~1GB"
        PINOKIO_APPS="xvoice,whisper"
        ;;
    2)
        OLLAMA_MODEL="qwen2.5:7b-instruct-q4_K_M"
        OLLAMA_MODEL_SIZE="~5GB"
        PINOKIO_APPS="xvoice,whisper,stable-diffusion"
        ;;
    3)
        OLLAMA_MODEL="qwen2.5:14b-instruct-q4_K_M"
        OLLAMA_MODEL_SIZE="~10GB"
        PINOKIO_APPS="xvoice,whisper,stable-diffusion,comfyui"
        ;;
    4)
        OLLAMA_MODEL="qwen2.5:7b-instruct-q4_K_M"
        OLLAMA_MODEL_SIZE="~5GB"
        PINOKIO_APPS="xvoice,whisper,stable-diffusion"
        IS_HUB=true
        ;;
esac

echo "  → Ollama model: $OLLAMA_MODEL ($OLLAMA_MODEL_SIZE)"
echo "  → Pinokio apps: $PINOKIO_APPS"
echo "  → Hub mode: ${IS_HUB:-false}"

# ── 4. Configure Ollama ─────────────────────────────────────────────────────

echo ""
echo "[4/6] Configuring Ollama..."

# Install Ollama if not present
if ! command -v ollama &>/dev/null; then
    echo "  Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -5
fi

# Start Ollama service
if ! systemctl is-active --quiet ollama 2>/dev/null; then
    sudo systemctl enable --now ollama 2>&1 | tail -3
fi

# Wait for Ollama to be ready
echo "  Waiting for Ollama to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        echo "  Ollama is ready"
        break
    fi
    sleep 2
done

# Pull the selected model
echo "  Pulling model: $OLLAMA_MODEL (this may take a while)..."
ollama pull "$OLLAMA_MODEL" 2>&1 | tail -3
echo "  ✓ Model ready: $OLLAMA_MODEL"

# ── 5. Configure Pinokio ────────────────────────────────────────────────────

echo ""
echo "[5/6] Configuring Pinokio..."

# Install Pinokio if not present
PINOKIO_DIR="/opt/pinokio"
if [ ! -d "$PINOKIO_DIR" ]; then
    echo "  Installing Pinokio..."
    # Pinokio is an Electron app — download latest release
    PINOKIO_URL="https://github.com/pinokiocomputer/pinokio/releases/latest/download/pinokio-linux-x64.zip"
    mkdir -p "$PINOKIO_DIR"
    curl -fsSL "$PINOKIO_URL" -o /tmp/pinokio.zip 2>&1 | tail -3
    unzip -q /tmp/pinokio.zip -d "$PINOKIO_DIR"
    rm -f /tmp/pinokio.zip
    echo "  ✓ Pinokio installed"
else
    echo "  ✓ Pinokio already installed"
fi

# Create Pinokio app bundle config
mkdir -p "$PINOKIO_DIR/catalog"
cat > "$PINOKIO_DIR/catalog/golden-image-apps.json" <<EOF
{
  "name": "Golden Image App Bundle",
  "version": "1.0.0",
  "apps": [
    {"name": "xvoice", "description": "Multilingual TTS + voice cloning", "required": true},
    {"name": "whisper", "description": "Speech-to-text transcription", "required": true},
    {"name": "stable-diffusion", "description": "Image generation", "required": false},
    {"name": "comfyui", "description": "Advanced image/video generation", "required": false}
  ]
}
EOF
echo "  ✓ App bundle configured"

# ── 6. Configure Shared MCP Bridge ──────────────────────────────────────────

echo ""
echo "[6/6] Configuring shared MCP bridge..."

BRIDGE_DIR="/opt/shared-bridge"
if [ ! -d "$BRIDGE_DIR" ]; then
    mkdir -p "$BRIDGE_DIR"
    # Copy from USB or download from hub
    if [ -f /media/*/shared-mcp-server/server.py ]; then
        cp -r /media/*/shared-mcp-server/* "$BRIDGE_DIR/" 2>/dev/null || true
    fi
fi

# Configure bridge mode
if [ "${IS_HUB:-false}" = true ]; then
    BRIDGE_MODE="hub"
    echo "  → Running as HUB (will serve other devices)"
else
    BRIDGE_MODE="spoke"
    # Try to discover hub on local network
    if [ "$HAS_NETWORK" = true ]; then
        echo "  → Searching for hub on local network..."
        # Scan for the hub's MCP bridge port (default 3000)
        SUBNET=$(echo "$LOCAL_IP" | cut -d. -f1-3)
        for i in $(seq 1 254); do
            if timeout 1 bash -c "echo >/dev/tcp/${SUBNET}.${i}/3000" 2>/dev/null; then
                HUB_IP="${SUBNET}.${i}"
                echo "  → Found hub at: $HUB_IP"
                break
            fi
        done
    fi
    if [ -z "$HUB_IP" ]; then
        echo "  → No hub found — running standalone"
        BRIDGE_MODE="standalone"
    fi
fi

# Write bridge config
cat > "$BRIDGE_DIR/config.json" <<EOF
{
  "mode": "$BRIDGE_MODE",
  "hub_ip": "${HUB_IP:-}",
  "tier": $TIER,
  "model": "$OLLAMA_MODEL",
  "ollama_url": "http://localhost:11434",
  "mcp_port": 3000,
  "device_id": "$(hostname)-$(cat /etc/machine-id 2>/dev/null | cut -c1-8)",
  "hardware": {
    "ram_gb": $TOTAL_RAM_GB,
    "cpu_cores": $CPU_CORES,
    "gpu": "$GPU_INFO",
    "gpu_vram_mb": $GPU_VRAM_MB
  }
}
EOF

echo "  → Bridge mode: $BRIDGE_MODE"
echo "  → Device ID: $(hostname)-$(cat /etc/machine-id 2>/dev/null | cut -c1-8)"

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Golden Image Setup Complete!"
echo "=========================================="
echo ""
echo "  Hardware Profile:"
echo "    Tier:        $TIER ($TIER_NAME)"
echo "    RAM:         ${TOTAL_RAM_GB}GB"
echo "    CPU:         $CPU_MODEL ($CPU_CORES cores)"
echo "    GPU:         $GPU_INFO (${GPU_VRAM_MB}MB VRAM)"
echo "    Storage:     ${FREE_DISK_GB}GB free"
echo ""
echo "  Software:"
echo "    Ollama:      $OLLAMA_MODEL"
echo "    Pinokio:     $PINOKIO_APPS"
echo "    Bridge:      $BRIDGE_MODE"
echo "    Hub IP:      ${HUB_IP:-none}"
echo ""
echo "  Log: $LOG"
echo ""
echo "  Reboot to start using the system."
echo "=========================================="

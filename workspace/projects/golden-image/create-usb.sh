#!/usr/bin/env bash
# create-usb.sh — Create Bootable Golden Image USB Drive
# Usage: sudo ./create-usb.sh /dev/sdX [USB_SIZE_GB]
# WARNING: This will erase all data on the target device!

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

UBUNTU_ISO_URL="https://releases.ubuntu.com/24.04/ubuntu-24.04.1-live-server-amd64.iso"
UBUNTU_ISO_SHA256="e240e4b801f7bb68c20d1356b609689218f207ba7fb0e237f44490a0be709111"
MIN_USB_SIZE_GB=16
RECOMMENDED_USB_SIZE_GB=64
PERSISTENCE_SIZE_GB=8
SWAP_SIZE_GB=2

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Pre-flight ───────────────────────────────────────────────────────────────

if [ $# -lt 1 ]; then
    echo "Usage: sudo $0 /dev/sdX [USB_SIZE_GB]"
    echo ""
    echo "This will ERASE the target USB drive."
    echo "Available USB drives:"
    lsblk -d -o NAME,SIZE,MODEL,TRAN | grep -i "usb\|removable" 2>/dev/null || echo "  (run 'lsblk' to see all drives)"
    exit 1
fi

TARGET_DEV="$1"
USB_SIZE_GB="${2:-$RECOMMENDED_USB_SIZE_GB}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -b "$TARGET_DEV" ]; then
    error "Device $TARGET_DEV is not a block device."
fi

# Safety check — don't overwrite system disks
if findmnt "$TARGET_DEV"1 &>/dev/null || findmnt "$TARGET_DEV"2 &>/dev/null; then
    error "Device $TARGET_DEV appears to be mounted. Aborting for safety."
fi

if echo "$TARGET_DEV" | grep -qE "nvme|sda|sdb"; then
    warn "WARNING: $TARGET_DEV looks like a system disk!"
    read -p "Are you ABSOLUTELY SURE? Type 'YES' to continue: " CONFIRM
    [ "$CONFIRM" = "YES" ] || error "Aborted."
fi

log "Creating bootable golden image USB on $TARGET_DEV (${USB_SIZE_GB}GB)"

# ── Step 1: Download Ubuntu ISO ──────────────────────────────────────────────

ISO_PATH="/tmp/ubuntu-24.04-live-server-amd64.iso"

if [ -f "$ISO_PATH" ]; then
    EXISTING_SHA=$(sha256sum "$ISO_PATH" | cut -d' ' -f1)
    if [ "$EXISTING_SHA" = "$UBUNTU_ISO_SHA256" ]; then
        log "✓ Ubuntu ISO already downloaded and verified"
    else
        log "Re-downloading Ubuntu ISO (checksum mismatch)..."
        rm -f "$ISO_PATH"
    fi
fi

if [ ! -f "$ISO_PATH" ]; then
    log "Downloading Ubuntu 24.04 LTS Server ISO..."
    wget -q --show-progress "$UBUNTU_ISO_URL" -O "$ISO_PATH"
    
    log "Verifying ISO checksum..."
    DOWNLOAD_SHA=$(sha256sum "$ISO_PATH" | cut -d' ' -f1)
    if [ "$DOWNLOAD_SHA" != "$UBUNTU_ISO_SHA256" ]; then
        error "ISO checksum mismatch! Expected: $UBUNTU_ISO_SHA256 Got: $DOWNLOAD_SHA"
    fi
    log "✓ ISO verified"
fi

# ── Step 2: Write ISO to USB ─────────────────────────────────────────────────

log "Writing ISO to $TARGET_DEV (this will take a few minutes)..."

# Unmount any partitions
umount "$TARGET_DEV"* 2>/dev/null || true

# Write ISO
dd if="$ISO_PATH" of="$TARGET_DEV" bs=4M status=progress conv=fdatasync
sync

log "✓ ISO written to USB"

# ── Step 3: Create Persistent Partition ──────────────────────────────────────

log "Creating persistent partition (${PERSISTENCE_SIZE_GB}GB)..."

# Re-read partition table
partprobe "$TARGET_DEV"
sleep 2

# Get available space after ISO
ISO_SIZE_BYTES=$(stat -c%s "$ISO_PATH")
TOTAL_DEV_BYTES=$(blockdev --getsize64 "$TARGET_DEV")
AVAILABLE_BYTES=$((TOTAL_DEV_BYTES - ISO_SIZE_BYTES))

# Create persistence partition (use remaining space or specified size)
PERSIST_BYTES=$((PERSISTENCE_SIZE_GB * 1024 * 1024 * 1024))
if [ "$AVAILABLE_BYTES" -lt "$PERSIST_BYTES" ]; then
    PERSIST_BYTES=$AVAILABLE_BYTES
    warn "Using all available space: $(numfmt --to=iec $PERSIST_BYTES)"
fi

# Create partition
echo 'type=83' | sfdisk "$TARGET_DEV" --force --append 2>&1 | tail -3 || {
    warn "sfdisk failed, trying parted..."
    parted "$TARGET_DEV" --script mkpart primary ext4 "${ISO_SIZE_BYTES}B" "-1" 2>&1 | tail -3
}

partprobe "$TARGET_DEV"
sleep 2

# Find the new partition (usually last partition)
NEW_PART="${TARGET_DEV}$(ls "$TARGET_DEV"* | tail -1 | grep -oP '\d+$')"
if [ ! -b "$NEW_PART" ]; then
    NEW_PART="${TARGET_DEV}p3"
fi

if [ ! -b "$NEW_PART" ]; then
    error "Could not find new partition. Check 'lsblk $TARGET_DEV' manually."
fi

log "Formatting persistent partition as ext4..."
mkfs.ext4 -L "golden-persist" "$NEW_PART" 2>&1 | tail -3

log "✓ Persistent partition ready: $NEW_PART"

# ── Step 4: Copy Golden Image Files ──────────────────────────────────────────

log "Copying golden image files to persistent partition..."

MOUNT_POINT="/tmp/golden-usb-mount"
mkdir -p "$MOUNT_POINT"
mount "$NEW_PART" "$MOUNT_POINT"

# Copy deployment scripts
mkdir -p "$MOUNT_POINT/golden-image"
cp "$SCRIPT_DIR/deploy.sh" "$MOUNT_POINT/golden-image/"
cp "$SCRIPT_DIR/golden-image-setup.sh" "$MOUNT_POINT/golden-image/"
cp "$SCRIPT_DIR/ARCHITECTURE.md" "$MOUNT_POINT/golden-image/" 2>/dev/null || true

# Copy shared MCP bridge
cp -r "$SCRIPT_DIR/../../" "$MOUNT_POINT/shared-mcp-server/" 2>/dev/null || true

# Create rootfs overlay structure
mkdir -p "$MOUNT_POINT/overlay/{opt,usr/local/bin,etc/golden-image,var/lib}"

# Pre-install Ollama models directory (will be populated on first boot)
mkdir -p "$MOUNT_POINT/overlay/var/lib/ollama"

# Create first-boot script
cat > "$MOUNT_POINT/golden-image/first-boot.sh" <<'FIRSTBOOT'
#!/usr/bin/env bash
# First-boot setup — runs on every fresh boot from USB
FLAG="/var/lib/golden-image/first-boot-done"

if [ -f "$FLAG" ]; then
    echo "Golden image already configured. Run golden-image-setup.sh to re-configure."
    exit 0
fi

echo "Running first-boot setup..."
sleep 5  # Wait for network

# Run hardware detection and setup
bash /media/*/golden-image/golden-image-setup.sh || \
bash /mnt/*/golden-image/golden-image-setup.sh || \
echo "ERROR: Could not find golden-image-setup.sh on USB"

mkdir -p /var/lib/golden-image
touch "$FLAG"
echo "First-boot setup complete!"
FIRSTBOOT
chmod +x "$MOUNT_POINT/golden-image/first-boot.sh"

# Create README
cat > "$MOUNT_POINT/README.txt" <<'README'
╔══════════════════════════════════════════════════════════════╗
║           GOLDEN IMAGE — Targeted Design Agency             ║
║                    Business in a Box v1.0                    ║
╚══════════════════════════════════════════════════════════════╝

QUICK START:
1. Boot from this USB drive
2. Login: ubuntu / (no password on live session)
3. Run: sudo bash /media/*/golden-image/deploy.sh
4. Follow the prompts

Or for automatic setup:
  sudo bash /media/*/golden-image/golden-image-setup.sh

COMMANDS (after install):
  golden-image status     — Show system status
  golden-image models     — List installed models
  golden-image run MODEL  — Chat with a model
  golden-image stop/start  — Control Ollama

HARDWARE TIERS:
  Tier 1 (4-8GB RAM):   TinyLlama 1.1B — basic agent
  Tier 2 (8-16GB RAM):  Qwen 2.5 7B — full agent
  Tier 3 (16-32GB RAM): Qwen 2.5 14B — multimodal
  Tier 4 (32GB+ RAM):   Hub mode — serves other devices

NETWORK:
  When on the same network as the hub:
  - Automatically discovers and connects
  - Uses shared MCP bridge for coordination
  - Falls back to standalone mode if no hub found

SUPPORT:
  See ARCHITECTURE.md for full documentation
  See config.json after install for device-specific settings
README

# Unmount
umount "$MOUNT_POINT"
rmdir "$MOUNT_POINT" 2>/dev/null || true

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          GOLDEN IMAGE USB CREATION COMPLETE!                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Device:    $TARGET_DEV"
echo "  OS:        Ubuntu 24.04 LTS Server"
echo "  Persist:   ${PERSISTENCE_SIZE_GB}GB (golden-persist)"
echo "  Files:     deploy.sh, golden-image-setup.sh, shared-mcp-server/"
echo ""
echo "  NEXT STEPS:"
echo "  1. Boot any machine from this USB"
echo "  2. Run: sudo bash /media/*/golden-image/deploy.sh"
echo "  3. Or for auto-setup: sudo bash /media/*/golden-image/golden-image-setup.sh"
echo ""
echo "  To clone this USB to another drive:"
echo "  sudo dd if=$TARGET_DEV of=/dev/sdY bs=4M status=progress"
echo ""

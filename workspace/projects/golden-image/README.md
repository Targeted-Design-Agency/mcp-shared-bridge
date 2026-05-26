# Golden Image — Targeted Design Agency
# Business in a Box v1.0

## What This Is

A bootable USB drive containing a complete, portable AI agent system that:
- Boots on **any** x86_64 machine
- Auto-detects hardware and self-configures
- Runs **fully offline** — no cloud dependency
- Coordinates with other agents on the local network
- Delivers multilingual TTS, image generation, and agent reasoning

## Project Structure

```
golden-image/
├── ARCHITECTURE.md          # Full system design document
├── deploy.sh                # Master deployment script (1-click install)
├── golden-image-setup.sh    # Hardware detection + auto-configuration
├── create-usb.sh            # USB drive creation script
└── README.md                # This file
```

## Quick Deploy

### On a fresh machine (from USB):
```bash
sudo bash /media/*/golden-image/deploy.sh
```

### On any Linux machine (from network):
```bash
curl -fsSL https://targeted.design/golden-image/deploy.sh | bash
```

### Force specific configuration:
```bash
sudo bash deploy.sh --hub              # Configure as network hub
sudo bash deploy.sh --tier 2           # Force Standard tier
sudo bash deploy.sh --model qwen2.5:7b # Force specific model
sudo bash deploy.sh --offline          # Skip model downloads
```

### From USB drive (auto-detect):
```bash
sudo bash /media/*/golden-image/golden-image-setup.sh
```

## Creating a Bootable USB

You need:
- USB drive (64GB+ recommended, 16GB minimum)
- A Linux machine with sudo access
- Internet connection (to download Ubuntu ISO)

```bash
# Download and run
curl -fsSL https://targeted.design/golden-image/create-usb.sh -o create-usb.sh
sudo bash create-usb.sh /dev/sdX 64
```

Replace `/dev/sdX` with your USB device (check with `lsblk`).
**WARNING:** This erases the entire USB drive.

## Cloning an Existing USB

Once you have a working golden image USB, clone it to others:

```bash
# Find source and target devices
lsblk

# Clone (replace sdX and sdY with actual devices)
sudo dd if=/dev/sdX of=/dev/sdY bs=4M status=progress conv=fdatasync

# Expand persistence partition on larger drives
sudo parted /dev/sdY --script resizepart 3 100%
sudo e2fsck -f /dev/sdY3
sudo resize2fs /dev/sdY3
```

## Hardware Tiers

| Tier | RAM | GPU | Model | Use Case |
|------|-----|-----|-------|----------|
| 1 — Minimal | 4-8GB | Any/none | TinyLlama 1.1B | Chromebooks, old PCs |
| 2 — Standard | 8-16GB | 2-4GB | Qwen 2.5 7B | Mid-range laptops |
| 3 — Performance | 16-32GB | 4-8GB | Qwen 2.5 14B | Desktops, gaming laptops |
| 4 — Hub | 32GB+ | Any | Qwen 2.5 7B + cloud | Always-on server |

## What Runs Where

### On Every Device (Tier 1-4):
- ✅ Ollama (local model inference)
- ✅ Pinokio (AI app store)
- ✅ Golden Image Auto-Detect (hardware configuration)
- ✅ Shared MCP Bridge client (network coordination)
- ✅ Core agent system (OpenClaw lightweight)

### On Tier 2+:
- ✅ Stable Diffusion (image generation)
- ✅ Xvoice (multilingual TTS)

### On Tier 3+:
- ✅ ComfyUI (advanced image/video generation)
- ✅ Whisper (speech-to-text)

### On Hub (Tier 4 or --hub):
- ✅ Shared MCP Bridge server (coordinates all devices)
- ✅ Voice agent service
- ✅ Cloud API fallback for heavy models

## Network Architecture

```
┌─────────────────────┐
│   YOUR MACHINE      │
│   (Hub)             │
│   shared-bridge     │
│   voice agent       │
│   cloud API         │
└────────┬────────────┘
         │ WiFi/Ethernet
    ┌────┼────┐
    │    │    │
  ┌─┴─┐┌┴──┐┌┴──┐
  │USB││USB││USB│
  │ T1││ T2││ T1│
  └───┘└───┘└───┘
```

All devices auto-discover the hub on the local network. When no hub is found, devices run standalone.

## Security

- No cloud dependency for core functions
- All inference runs locally
- All coordination stays on local network
- Each USB is self-contained — no data leaks
- Optional: LUKS encryption on persistence partition

## For Replication

After building and testing, the full build process is documented in
`ARCHITECTURE.md`. Key files needed on each USB:

1. Ubuntu 24.04 LTS Server Live ISO (650MB)
2. Golden Image scripts (this project)
3. Shared MCP bridge (shared-mcp-server/)
4. Downloaded Ollama models (~1-10GB depending on tier)

## Version History

- v1.0 (2026-05-23) — Initial build
  - Hardware auto-detection with 4 tiers
  - Master deployment script
  - USB creation script
  - Hub-and-spoke network architecture
  - Shared MCP bridge integration

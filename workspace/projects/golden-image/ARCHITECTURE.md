# Golden Image Architecture — Targeted Design Agency
# Bootable AI Agent System v1.0

## Concept
A portable, bootable USB drive containing a complete AI agent system that:
- Boots on any x86_64 machine (PC, laptop, anything)
- Auto-detects hardware and configures appropriately
- Runs fully offline (no cloud dependency)
- Connects to the agency hub when on the same network
- Provides multilingual TTS, image gen, and agent coordination

## Hardware Tiers

### Tier 1: Minimal (4-8GB RAM, no GPU)
- **Target:** Kids' Chromebooks, old laptops
- **Local model:** TinyLlama 1.1B or Phi-2 2.7B
- **Capabilities:** Basic agent tasks, voice cloning (Xvoice lite), text generation
- **Storage:** ~2GB for OS + models
- **Boot mode:** Live USB with persistence

### Tier 2: Standard (8-16GB RAM, 2-4GB GPU)
- **Target:** Mid-range laptops
- **Local model:** Qwen 2.5 7B (quantized Q4)
- **Capabilities:** Full agent reasoning, TTS, basic image gen
- **Storage:** ~8GB for OS + models

### Tier 3: Performance (16-32GB RAM, 4-8GB GPU)
- **Target:** Modern desktops, gaming laptops
- **Local model:** Qwen 2.5 14B or Qwen 3.5 Omni (multimodal)
- **Capabilities:** Multimodal reasoning, voice agent, image/video gen
- **Storage:** ~20GB for OS + models

### Tier 4: Hub (Your Machine — 32GB RAM)
- **Target:** Always-on hub/server
- **Local model:** Qwen 2.5 7B + cloud API fallback for heavy tasks
- **Capabilities:** Runs the shared MCP bridge, serves other devices
- **Storage:** ~15GB for OS + models + data

## USB Drive Layout

```
┌────────────────────────────────────────────────────┐
│                  USB Drive (64GB+)                  │
├──────────────┬─────────────────────────────────────┤
│  EFI System  │         Persistent Storage           │
│  Partition   │                                     │
│  (500MB)     │  ┌─────────────────────────────┐    │
│              │  │  /boot  — GRUB bootloader    │    │
│              │  │  /     — Ubuntu 24.04 base   │    │
│              │  │  /home — User data, configs  │    │
│              │  │  /opt  — AI models + apps    │    │
│              │  │  /var  — Ollama, Pinokio     │    │
│              │  └─────────────────────────────┘    │
└──────────────┴─────────────────────────────────────┘
```

## Software Stack

| Layer | Component | Purpose |
|-------|-----------|---------|
| OS | Ubuntu 24.04 LTS (minimal) | Base system |
| Boot | GRUB2 + persistence | Boot on any machine |
| Runtime | Ollama 0.5+ | Local model inference |
| App Store | Pinokio | 1-click AI app installs |
| Agent | OpenClaw (lightweight) | Agent orchestration |
| Bridge | Shared MCP Server | Connect to agency hub |
| Voice | Xvoice | Multilingual TTS + cloning |
| Config | Golden Image Auto-Detect | Hardware-aware setup |

## Boot Process

```
1. BIOS/USB boot → GRUB → Ubuntu Live + persistence
2. First boot: hardware detection script runs
   ├─ Detect RAM (free -g)
   ├─ Detect GPU (lspci + vulkaninfo)
   ├─ Detect network (nmcli)
   │
3. Hardware profile generated
   ├─ Tier 1 (4-8GB, no GPU)
   ├─ Tier 2 (8-16GB, basic GPU)
   ├─ Tier 3 (16-32GB, good GPU)
   └─ Tier 4 (32GB+, server)
   │
4. Auto-configure
   ├─ Download/configure appropriate Ollama model
   ├─ Set up Pinokio with app bundle
   ├─ Configure shared MCP bridge
   └─ If on agency LAN: connect to hub
   │
5. Ready to use — all apps, all models, fully configured
```

## Hub-and-Spoke Network

```
┌─────────────────────────────────────────┐
│            YOUR MACHINE (Hub)            │
│  32GB RAM, FX-8320, Radeon HD 7750     │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │ Shared MCP Bridge (shared-bridge)│   │
│  │ Coordinates all agents           │   │
│  └──────────────┬───────────────────┘   │
│                 │                        │
│  ┌──────────────┴───────────────────┐   │
│  │ Ollama (local 7B + cloud API)    │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │ Voice Agent (Qwen 3.5 Omni)      │   │
│  │ targeted-design-voice.service    │   │
│  └──────────────────────────────────┘   │
└──────────────────┬──────────────────────┘
                   │ WiFi / Ethernet
        ┌──────────┼──────────┐
        │          │          │
   ┌────┴────┐ ┌───┴───┐ ┌───┴────┐
   │ USB     │ │ USB   │ │ USB    │
   │ Golden  │ │Golden │ │Golden  │
   │ Image   │ │Image  │ │Image   │
   │         │ │       │ │        │
   │ Kid 1   │ │Kid 2  │ │Kid 3   │
   │ Tier 1  │ │Tier 2 │ │Tier 1  │
   └─────────┘ └───────┘ └────────┘
```

## Security Model
- No cloud dependency for core functions
- All AI inference runs locally
- MCP bridge uses local network only (no internet exposure)
- Models stored encrypted at rest (LUKS optional)
- Each USB drive is self-contained — no data leaks between uses

## What's NOT on the image (cloud fallback)
- Large multimodal models (Qwen 3.5 Omni — too big for most devices)
- Heavy image/video generation (ComfyUI + SDXL — 10GB+)
- These stay on the hub machine or use cloud API

## Deployment Checklist
- [ ] Download Ubuntu 24.04 minimal ISO
- [ ] Create bootable USB with persistence
- [ ] Install base system + dependencies
- [ ] Install Ollama
- [ ] Install Pinokio
- [ ] Install shared-bridge MCP server
- [ ] Install OpenClaw (lightweight config)
- [ ] Bundle models per tier
- [ ] Write hardware detection + auto-config script
- [ ] Test boot on multiple machines
- [ ] Create cloning procedure (dd or clonezilla)
- [ ] Document the build

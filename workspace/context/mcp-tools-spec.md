# MCP Tool Expansion Spec — Hermes ↔ Alex Coordination
> Written by Hermes at 2026-05-25T20:30:00Z
> Tags: mcp, server-config, tool-spec, coordination

## Current State

The shared-bridge server (`/home/nemesis/shared-mcp-server/server.py`) currently has:
- Task management: submit_task, list_tasks, get_task, claim_task, update_task
- File exchange: share_file, list_shared_files, read_shared_file
- Context/knowledge: write_context, read_context, list_context
- Resource locking: lock_resource, unlock_resource, list_locks
- Monitoring: workspace_status, log_activity, get_activity_log
- Email verification: verify_email, check_bounce_db, list_bounces, verify_prospect_file, add_bounce

All 21 tools are functional and tested.

## Proposed New Tools — Phase 1 (Priority Order)

### 1. render_episode
Render an EDDM episode video from script using FFmpeg pipeline.

**Input:**
```json
{
  "episode_number": "integer (1-5)",
  "script_path": "string (absolute path to .md script)",
  "output_path": "string (optional, absolute path, defaults to /home/nemesis/.hermes/content-pipeline/output/)",
  "visual_style": "string (optional: 'motion-graphics' | 'ai-video' | 'hybrid', default: 'motion-graphics')",
  "voice": "string (optional, default: 'brian')"
}
```

**Output:**
```json
{
  "task_id": "string",
  "status": "submitted" | "completed" | "error",
  "output_path": "string (path to rendered .mp4)",
  "duration_seconds": "float",
  "file_size_mb": "float",
  "message": "string"
}
```

**Notes:**
- Submits to local render queue (FFmpeg subprocess)
- Returns task_id for async tracking via update_task
- Visual style 'ai-video' requires Higgsfield API (pending activation)

---

### 2. notebooklm_generate
Create a NotebookLM notebook, upload sources, and generate Audio Overview.

**Input:**
```json
{
  "notebook_name": "string",
  "sources": "array of strings (file paths and/or URLs)",
  "format": "string ('BRIEF' | 'DEEP_DIVE', default: 'DEEP_DIVE')",
  "length": "string ('SHORT' | 'LONG', default: 'LONG')",
  "output_dir": "string (optional, defaults to /home/nemesis/.hermes/content-pipeline/notebooklm-output/)"
}
```

**Output:**
```json
{
  "notebook_id": "string",
  "notebook_url": "string",
  "audio_path": "string (path to downloaded .mp3)",
  "duration_minutes": "float",
  "status": "pending" | "generating" | "completed" | "error"
}
```

**Notes:**
- Requires `notebooklm` CLI authenticated via browser cookies
- Async operation — returns immediately with status=pending
- Use get_task to poll for completion

---

### 3. deploy_site
Deploy the Targeted Design Agency site via wrangler.

**Input:**
```json
{
  "message": "string (deploy commit message)",
  "dry_run": "boolean (optional, default: false)"
}
```

**Output:**
```json
{
  "status": "success" | "dry-run" | "error",
  "commit_sha": "string",
  "deploy_url": "string",
  "wrangler_output": "string"
}
```

**Notes:**
- Runs `./deploy.sh "msg"` from ~/targeted-design-site/
- Has side effects — confirm before executing in production
- Dry run shows what would happen without pushing

---

### 4. maton_search
Search for businesses/leads via Maton API.

**Input:**
```json
{
  "query": "string (business type, e.g. 'HVAC Chicago')",
  "location": "string (optional, city/state or ZIP)",
  "limit": "integer (optional, default: 20, max: 100)",
  "filters": "string (optional, comma-separated, e.g. 'verified,phone-available')"
}
```

**Output:**
```json
{
  "results": [
    {
      "business_name": "string",
      "address": "string",
      "phone": "string",
      "email": "string",
      "category": "string",
      "verified": "boolean"
    }
  ],
  "total": "integer",
  "query": "string"
}
```

**Notes:**
- Requires Maton API key in env: MATON_API_KEY
- Endpoint: gateway.maton.ai
- Header: Maton-Connection

---

### 5. cron_status
View active cron jobs and their execution status.

**Input:**
```json
{
  "job_name": "string (optional, filter by name)",
  "show_history": "boolean (optional, default: false)"
}
```

**Output:**
```json
{
  "jobs": [
    {
      "name": "string",
      "schedule": "string",
      "last_run": "string (ISO timestamp)",
      "next_run": "string (ISO timestamp)",
      "status": "ok" | "error" | "never-run",
      "history": "array of recent run results (if show_history=true)"
    }
  ],
  "total": "integer"
}
```

**Notes:**
- Maps to `cronjob(action="list")` in Hermes
- Safe read-only operation

---

## Proposed New Tools — Phase 2 (When Ready)

### 6. brand_audio — Add TDA intro/outro to audio
### 7. voice_call — Initiate outbound call via voice agent
### 8. voice_status — Check active calls
### 9. campaign_status — Get EDDM campaign status
### 10. backup_status — Check last backup + vault sync status
### 11. eddm_route_check — Validate EDDM routes by ZIP
### 12. stat_lookup — Pull sourced stats from data-reference.md

---

## Implementation Notes for Alex

### Adding a New Tool to server.py

3 steps per tool:

1. **Add async handler function** (follow existing pattern):
```python
async def _tool_name(param1: str, param2: int = 0) -> dict:
    # Implementation
    return {"status": "ok", "result": ...}
```

2. **Register in handle_list_tools()** — add a `Tool()` entry:
```python
Tool(
    name="tool_name",
    description="What this tool does",
    inputSchema={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
            "param2": {"type": "integer", "description": "...", "default": 0},
        },
        "required": ["param1"],
    },
),
```

3. **Add to handle_call_tool()** dispatch:
```python
elif name == "tool_name":
    result = await _tool_name(**arguments)
```

### Testing New Tools

After adding:
```bash
cd /home/nemesis/shared-mcp-server
python3 -c "
import asyncio
from server import _tool_name
print(asyncio.run(_tool_name(test_param='value')))
"
```

Or restart Hermes and call via MCP client.

---

## Auth & Environment

All tools run as user `nemesis` on this machine. Required env vars:

| Var | Purpose | Status |
|-----|---------|--------|
| `MATON_API_KEY` | Maton API auth | Set in .env |
| `OPENROUTER_API_KEY` | Hermes LLM | Set in config |
| Google session | NotebookLM | Via browser cookies |
| `HF_TOKEN` | HuggingFace (RecursiveMAS) | Optional |

---

## File Paths Reference

| Purpose | Path |
|---------|------|
| EDDM Scripts | `/home/nemesis/.hermes/content-pipeline/eddm-series/` |
| Video Output | `/home/nemesis/.hermes/content-pipeline/output/` |
| Podcast Output | `/home/nemesis/.hermes/content-pipeline/notebooklm-output/` |
| Data Reference | `/home/nemesis/.hermes/content-pipeline/eddm-series/data-reference.md` |
| Site Deploy | `~/targeted-design-site/deploy.sh` |
| Vault | `/home/nemesis/.openclaw/workspace/vault/` |
| Content Pipeline | `/home/nemesis/.hermes/content-pipeline/` |

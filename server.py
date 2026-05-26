#!/usr/bin/env python3
"""Shared MCP Server: Hermes ↔ Alex Sterling Collaboration Bridge

Provides tools for:
- Task management (submit, claim, update, complete, list)
- File exchange with metadata
- Shared context/knowledge base (read/write/query)
- Resource locking (prevent duplicate work)
- Activity logging & audit trail
- Workspace status dashboard

Uses MCP stdio transport. Backed by filesystem at /home/nemesis/shared-mcp-server/workspace/

Usage:
  python server.py
"""

import os
import json
import asyncio
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Import email verification modules
_SCRIPTS_DIR = Path("/home/nemesis/.openclaw/workspace/scripts")
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

def _import_email_tools():
    """Lazy import email tools to avoid startup errors"""
    try:
        from verify_emails import verify_email as _vef, load_bounce_database as _lbdb
        return _vef, _lbdb
    except ImportError as e:
        print(f"WARN: Could not import verify_emails: {e}", file=sys.stderr)
        return None, None

def _import_prospect_tools():
    """Lazy import prospect verification tools"""
    try:
        from verify_prospects import parse_prospect_file as _ppf, verify_prospect as _vp, load_bounce_database as _lbdb2
        return _ppf, _vp, _lbdb2
    except ImportError as e:
        print(f"WARN: Could not import verify_prospects: {e}", file=sys.stderr)
        return None, None, None

_verify_email_func, _load_bounce_db = _import_email_tools()
_parse_prospect_file, _verify_prospect, _load_bounce_db2 = _import_prospect_tools()

# ── Paths ────────────────────────────────────────────────────────────────────

BASE = Path("/home/nemesis/shared-mcp-server")
WORKSPACE = BASE / "workspace"
INBOX = WORKSPACE / "inbox"
OUTBOX = WORKSPACE / "outbox"
PROJECTS = WORKSPACE / "projects"
CONTEXT = WORKSPACE / "context"
LOGS = WORKSPACE / "logs"
LOCKS = BASE / "locks"
BACKUPS = BASE / "backups"

for p in [INBOX, OUTBOX, PROJECTS, CONTEXT, LOGS, LOCKS, BACKUPS]:
    p.mkdir(parents=True, exist_ok=True)

server = Server("shared-bridge")

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _log(action: str, detail: str, agent: str = "unknown"):
    log_file = LOGS / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    entry = {"ts": _now(), "action": action, "agent": agent, "detail": detail}
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)

def _write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

def _task_path(task_id: str) -> Path:
    return INBOX / f"task_{task_id}.json"

def _all_tasks() -> list[dict]:
    tasks = []
    for p in sorted(INBOX.glob("task_*.json")):
        t = _read_json(p)
        if t:
            tasks.append(t)
    return tasks

def _generate_id(prefix: str = "task") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    h = hashlib.sha256(f"{ts}{os.urandom(8).hex()}".encode()).hexdigest()[:8]
    return f"{prefix}_{ts}_{h}"

# ── Tool: submit_task ────────────────────────────────────────────────────────

async def _submit_task(
    title: str,
    description: str,
    from_agent: str,
    to_agent: str,
    priority: str = "medium",
    context: str = "",
    expected_output: str = "",
    tags: str = "",
    project: str = "",
) -> dict:
    task_id = _generate_id()
    task = {
        "id": task_id,
        "title": title,
        "description": description,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "priority": priority,
        "status": "pending",
        "context": context,
        "expected_output": expected_output,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "project": project,
        "created_at": _now(),
        "updated_at": _now(),
        "claimed_by": None,
        "claimed_at": None,
        "completed_at": None,
        "result": None,
        "artifacts": [],
        "notes": [],
    }
    _write_json(_task_path(task_id), task)
    _log("submit_task", f"{task_id}: {title} ({from_agent} → {to_agent})", from_agent)

    # Also write a human-readable summary
    summary_path = INBOX / f"task_{task_id}.md"
    with open(summary_path, "w") as f:
        f.write(f"# {title}\n\n")
        f.write(f"**ID:** {task_id}  \n")
        f.write(f"**From:** {from_agent} → **To:** {to_agent}  \n")
        f.write(f"**Priority:** {priority}  \n")
        f.write(f"**Status:** pending  \n")
        f.write(f"**Created:** {task['created_at']}  \n\n")
        f.write(f"## Description\n\n{description}\n\n")
        if context:
            f.write(f"## Context\n\n{context}\n\n")
        if expected_output:
            f.write(f"## Expected Output\n\n{expected_output}\n\n")
        if project:
            f.write(f"## Project\n\n{project}\n\n")

    return {"task_id": task_id, "status": "submitted", "message": f"Task '{title}' submitted to {to_agent}"}

# ── Tool: list_tasks ─────────────────────────────────────────────────────────

async def _list_tasks(
    status_filter: str = "",
    agent_filter: str = "",
    project_filter: str = "",
) -> list[dict]:
    tasks = _all_tasks()
    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]
    if agent_filter:
        tasks = [t for t in tasks if t.get("to_agent") == agent_filter or t.get("from_agent") == agent_filter]
    if project_filter:
        tasks = [t for t in tasks if t.get("project") == project_filter]
    # Sort: pending first, then by priority
    priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    status_order = {"claimed": 0, "pending": 1, "in_progress": 2, "completed": 3, "cancelled": 4}
    tasks.sort(key=lambda t: (status_order.get(t["status"], 9), priority_order.get(t.get("priority", "medium"), 2)))
    return tasks

# ── Tool: get_task ───────────────────────────────────────────────────────────

async def _get_task(task_id: str) -> dict:
    path = _task_path(task_id)
    task = _read_json(path)
    if not task:
        return {"error": f"Task {task_id} not found"}
    return task

# ── Tool: update_task ────────────────────────────────────────────────────────

async def _update_task(
    task_id: str,
    agent: str,
    status: str = "",
    result: str = "",
    note: str = "",
    add_artifact: str = "",
) -> dict:
    path = _task_path(task_id)
    task = _read_json(path)
    if not task:
        return {"error": f"Task {task_id} not found"}

    if status:
        task["status"] = status
        if status == "completed":
            task["completed_at"] = _now()
    if result:
        task["result"] = result
    if note:
        task["notes"].append({"ts": _now(), "agent": agent, "note": note})
    if add_artifact:
        task["artifacts"].append({"ts": _now(), "path": add_artifact, "added_by": agent})

    task["updated_at"] = _now()
    _write_json(path, task)
    _log("update_task", f"{task_id}: status={status}, note={bool(note)}, artifact={bool(add_artifact)}", agent)
    return {"task_id": task_id, "status": task["status"], "message": "Task updated"}

# ── Tool: claim_task ─────────────────────────────────────────────────────────

async def _claim_task(task_id: str, agent: str) -> dict:
    path = _task_path(task_id)
    task = _read_json(path)
    if not task:
        return {"error": f"Task {task_id} not found"}
    if task["status"] in ("claimed", "in_progress"):
        return {"error": f"Task {task_id} already claimed by {task.get('claimed_by')}"}
    if task["status"] in ("completed", "cancelled"):
        return {"error": f"Task {task_id} is {task['status']}"}

    task["status"] = "claimed"
    task["claimed_by"] = agent
    task["claimed_at"] = _now()
    task["updated_at"] = _now()
    _write_json(path, task)
    _log("claim_task", f"{task_id} claimed by {agent}", agent)
    return {"task_id": task_id, "status": "claimed", "message": f"Task claimed by {agent}"}

# ── Tool: share_file ─────────────────────────────────────────────────────────

async def _share_file(
    file_path: str,
    from_agent: str,
    to_agent: str = "",
    description: str = "",
    task_id: str = "",
) -> dict:
    src = Path(file_path)
    if not src.exists():
        return {"error": f"File not found: {file_path}"}

    # Copy to outbox with metadata
    dest_name = f"{_generate_id('file')}_{src.name}"
    dest = OUTBOX / dest_name
    import shutil
    shutil.copy2(src, dest)

    meta = {
        "original_path": str(src),
        "shared_path": str(dest),
        "filename": src.name,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "description": description,
        "task_id": task_id,
        "shared_at": _now(),
        "size": src.stat().st_size,
    }
    _write_json(OUTBOX / f"{dest_name}.meta.json", meta)
    _log("share_file", f"{src.name} ({from_agent} → {to_agent or 'all'})", agent=from_agent)
    return {"shared_path": str(dest), "filename": src.name, "message": f"File shared: {src.name}"}

# ── Tool: list_shared_files ──────────────────────────────────────────────────

async def _list_shared_files(agent_filter: str = "") -> list[dict]:
    files = []
    for meta_path in sorted(OUTBOX.glob("*.meta.json")):
        meta = _read_json(meta_path)
        if meta:
            if agent_filter and meta.get("to_agent") and meta["to_agent"] != agent_filter:
                continue
            files.append(meta)
    return files

# ── Tool: read_shared_file ───────────────────────────────────────────────────

async def _read_shared_file(shared_path: str) -> dict:
    p = Path(shared_path)
    if not p.exists():
        return {"error": f"File not found: {shared_path}"}
    meta_path = Path(f"{shared_path}.meta.json")
    meta = _read_json(meta_path) or {}
    try:
        content = p.read_text()
    except Exception:
        content = "<binary file>"
    return {"metadata": meta, "content": content[:10000]}

# ── Tool: write_context ──────────────────────────────────────────────────────

async def _write_context(
    key: str,
    content: str,
    agent: str,
    description: str = "",
    tags: str = "",
) -> dict:
    safe_key = key.replace("/", "_").replace(" ", "_")
    ctx_path = CONTEXT / f"{safe_key}.md"
    entry = {
        "key": key,
        "description": description,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "updated_by": agent,
        "updated_at": _now(),
    }
    with open(ctx_path, "w") as f:
        f.write(f"# {key}\n\n")
        f.write(f"> Updated by **{agent}** at {entry['updated_at']}  \n")
        if description:
            f.write(f"> {description}\n\n")
        else:
            f.write("\n")
        f.write(content)
    _write_json(CONTEXT / f"{safe_key}.meta.json", entry)
    _log("write_context", f"{key} by {agent}", agent)
    return {"key": key, "path": str(ctx_path), "message": f"Context '{key}' written"}

# ── Tool: read_context ───────────────────────────────────────────────────────

async def _read_context(key: str = "", tag: str = "") -> list[dict]:
    results = []
    for meta_path in sorted(CONTEXT.glob("*.meta.json")):
        meta = _read_json(meta_path)
        if not meta:
            continue
        if key and meta.get("key") != key:
            continue
        if tag and tag not in meta.get("tags", []):
            continue
        ctx_path = CONTEXT / f"{meta_path.stem}.md"
        content = ctx_path.read_text() if ctx_path.exists() else ""
        results.append({"metadata": meta, "content": content[:5000]})
    return results

# ── Tool: list_context ───────────────────────────────────────────────────────

async def _list_context() -> list[dict]:
    items = []
    for meta_path in sorted(CONTEXT.glob("*.meta.json")):
        meta = _read_json(meta_path)
        if meta:
            items.append(meta)
    return items

# ── Tool: workspace_status ───────────────────────────────────────────────────

async def _workspace_status() -> dict:
    tasks = _all_tasks()
    status_counts = {}
    for t in tasks:
        s = t["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    inbox_files = list(INBOX.glob("task_*.json"))
    outbox_files = list(OUTBOX.glob("*.meta.json"))
    context_files = list(CONTEXT.glob("*.meta.json"))

    # Recent activity
    log_file = LOGS / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    recent_activity = []
    if log_file.exists():
        with open(log_file) as f:
            lines = f.readlines()
            for line in lines[-20:]:
                try:
                    recent_activity.append(json.loads(line))
                except Exception:
                    pass

    # Disk usage
    import shutil as sh
    total, used, free = sh.disk_usage("/home/nemesis")

    return {
        "tasks": {
            "total": len(tasks),
            "by_status": status_counts,
        },
        "files": {
            "inbox": len(inbox_files),
            "outbox": len(outbox_files),
            "context_entries": len(context_files),
        },
        "recent_activity": recent_activity,
        "disk": {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
        },
        "timestamp": _now(),
    }

# ── Tool: lock_resource ──────────────────────────────────────────────────────

async def _lock_resource(resource: str, agent: str, reason: str = "") -> dict:
    lock_path = LOCKS / f"{resource.replace('/', '_').replace(' ', '_')}.lock"
    if lock_path.exists():
        existing = _read_json(lock_path)
        if existing:
            return {"error": f"Resource '{resource}' already locked by {existing.get('agent')} since {existing.get('since')}"}
    lock_data = {
        "resource": resource,
        "agent": agent,
        "since": _now(),
        "reason": reason,
    }
    _write_json(lock_path, lock_data)
    _log("lock_resource", f"{resource} locked by {agent}", agent)
    return {"resource": resource, "locked": True, "by": agent}

# ── Tool: unlock_resource ────────────────────────────────────────────────────

async def _unlock_resource(resource: str, agent: str) -> dict:
    lock_path = LOCKS / f"{resource.replace('/', '_').replace(' ', '_')}.lock"
    if not lock_path.exists():
        return {"error": f"Resource '{resource}' is not locked"}
    existing = _read_json(lock_path)
    if existing and existing.get("agent") != agent:
        return {"error": f"Resource '{resource}' locked by {existing.get('agent')}, not {agent}"}
    lock_path.unlink()
    _log("unlock_resource", f"{resource} unlocked by {agent}", agent)
    return {"resource": resource, "locked": False}

# ── Tool: list_locks ─────────────────────────────────────────────────────────

async def _list_locks() -> list[dict]:
    locks = []
    for p in sorted(LOCKS.glob("*.lock")):
        data = _read_json(p)
        if data:
            locks.append(data)
    return locks

# ── Tool: log_activity ───────────────────────────────────────────────────────

async def _log_activity(action: str, detail: str, agent: str) -> dict:
    _log(action, detail, agent)
    return {"logged": True, "action": action}

# ── Tool: get_activity_log ───────────────────────────────────────────────────

async def _get_activity_log(date: str = "", limit: int = 50) -> list[dict]:
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS / f"{date}.jsonl"
    if not log_file.exists():
        return []
    entries = []
    with open(log_file) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-limit:]

# ── Tool: tda_get_dashboard_stats ──────────────────────────────────────────

async def _mcp_tda_get_dashboard_stats() -> dict:
    """Return key TDA OS dashboard metrics: active clients, pipeline value, emails sent, bounces, tools built."""
    from datetime import datetime, timezone
    import re

    stats = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_clients": 0,
        "pipeline_value": 0,
        "emails_sent_today": 0,
        "emails_bounced_today": 0,
        "tools_built": 0,
    }

    # Count active clients from agency/clients directory
    clients_dir = Path("/home/nemesis/.openclaw/workspace/agency/clients")
    if clients_dir.exists():
        stats["active_clients"] = len([d for d in clients_dir.iterdir() if d.is_dir()])

    # Pipeline value from revenue-state.json
    revenue_file = Path("/home/nemesis/.openclaw/workspace/memory/revenue-state.json")
    if revenue_file.exists():
        revenue_data = _read_json(revenue_file)
        if revenue_data:
            pipeline = revenue_data.get("pipeline", {})
            prospects = pipeline.get("prospects", [])
            stats["pipeline_value"] = sum(p.get("value", 0) for p in prospects)
            stats["pipeline_prospects"] = len(prospects)
            mrr = revenue_data.get("mrr", {})
            stats["mrr_current"] = mrr.get("current", 0)
            stats["mrr_target"] = mrr.get("target", 0)

    # Email stats from today's activity log
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS / f"{today}.jsonl"
    if log_file.exists():
        with open(log_file) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    action = entry.get("action", "")
                    if "email" in action.lower() or "send" in action.lower():
                        stats["emails_sent_today"] += 1
                    if "bounce" in action.lower():
                        stats["emails_bounced_today"] += 1
                except Exception:
                    pass

    # Tools built from ai-tool-builder directory
    tool_builder_dir = Path("/home/nemesis/.openclaw/workspace/ai-tool-builder")
    if tool_builder_dir.exists():
        # Count Python files in utils/ as proxy for tools built
        utils_dir = tool_builder_dir / "utils"
        if utils_dir.exists():
            stats["tools_built"] = len(list(utils_dir.glob("*.py")))

    # Also count shared-mcp-server tools
    stats["mcp_tools_available"] = 38  # 32 existing + 6 new TDA tools

    return stats


# ── Tool: tda_get_agent_status ──────────────────────────────────────────────

async def _mcp_tda_get_agent_status() -> dict:
    """Return status of all TDA agents based on recent activity logs."""
    from datetime import datetime, timezone

    agents = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Scan last 7 days of logs for agent activity
    log_dir = LOGS
    if log_dir.exists():
        log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:7]
        for log_file in log_files:
            try:
                with open(log_file) as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            agent_name = entry.get("agent", "unknown")
                            if agent_name not in agents:
                                agents[agent_name] = {
                                    "last_seen": entry.get("ts"),
                                    "actions": 0,
                                    "recent_actions": [],
                                }
                            agents[agent_name]["actions"] += 1
                            if len(agents[agent_name]["recent_actions"]) < 5:
                                agents[agent_name]["recent_actions"].append({
                                    "action": entry.get("action"),
                                    "detail": entry.get("detail", "")[:100],
                                    "ts": entry.get("ts"),
                                })
                            # Update last_seen to most recent
                            if entry.get("ts", "") > agents[agent_name].get("last_seen", ""):
                                agents[agent_name]["last_seen"] = entry.get("ts")
                        except Exception:
                            pass
            except Exception:
                pass

    # Determine status based on recency
    now = datetime.now(timezone.utc)
    for name, info in agents.items():
        last_seen_str = info.get("last_seen", "")
        try:
            if last_seen_str:
                last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                delta = (now - last_seen).total_seconds()
                if delta < 3600:
                    info["status"] = "active"
                elif delta < 86400:
                    info["status"] = "idle"
                else:
                    info["status"] = "offline"
            else:
                info["status"] = "unknown"
        except Exception:
            info["status"] = "unknown"

    return {
        "agents": agents,
        "total_agents": len(agents),
        "active_count": sum(1 for a in agents.values() if a.get("status") == "active"),
        "timestamp": now.isoformat(),
    }


# ── Tool: tda_get_pipeline_summary ──────────────────────────────────────────

async def _mcp_tda_get_pipeline_summary() -> dict:
    """Return pipeline grouped by status with counts and values."""
    from datetime import datetime, timezone

    summary = {
        "by_status": {},
        "total_value": 0,
        "total_prospects": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    revenue_file = Path("/home/nemesis/.openclaw/workspace/memory/revenue-state.json")
    if not revenue_file.exists():
        return summary

    revenue_data = _read_json(revenue_file)
    if not revenue_data:
        return summary

    pipeline = revenue_data.get("pipeline", {})
    prospects = pipeline.get("prospects", [])

    for prospect in prospects:
        status = prospect.get("status", "unknown")
        value = prospect.get("value", 0)

        if status not in summary["by_status"]:
            summary["by_status"][status] = {
                "count": 0,
                "total_value": 0,
                "prospects": [],
            }

        summary["by_status"][status]["count"] += 1
        summary["by_status"][status]["total_value"] += value
        summary["by_status"][status]["prospects"].append({
            "name": prospect.get("name", "Unknown"),
            "contact": prospect.get("contact", ""),
            "tier": prospect.get("tier", "standard"),
            "value": value,
            "days_since_contact": prospect.get("days_since_contact", 0),
        })
        summary["total_value"] += value
        summary["total_prospects"] += 1

    # MRR info
    mrr = revenue_data.get("mrr", {})
    summary["mrr"] = {
        "current": mrr.get("current", 0),
        "target": mrr.get("target", 0),
        "breakdown": mrr.get("breakdown", {}),
    }

    return summary


# ── Tool: tda_get_recent_activity ───────────────────────────────────────────

async def _mcp_tda_get_recent_activity(limit: int = 30) -> dict:
    """Return recent actions across all systems (MCP logs, pipeline logs, etc.)."""
    from datetime import datetime, timezone

    activities = []

    # 1. MCP activity logs (last 3 days)
    log_dir = LOGS
    if log_dir.exists():
        log_files = sorted(log_dir.glob("*.jsonl"), reverse=True)[:3]
        for log_file in log_files:
            try:
                with open(log_file) as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            activities.append({
                                "source": "mcp",
                                "timestamp": entry.get("ts"),
                                "agent": entry.get("agent", "unknown"),
                                "action": entry.get("action"),
                                "detail": entry.get("detail", "")[:200],
                            })
                        except Exception:
                            pass
            except Exception:
                pass

    # 2. Pipeline automation log
    pipeline_log = Path("/home/nemesis/.openclaw/workspace/agency/operations/pipeline-automation.log")
    if pipeline_log.exists():
        try:
            with open(pipeline_log) as f:
                lines = f.readlines()
                for line in lines[-20:]:
                    line = line.strip()
                    if line:
                        activities.append({
                            "source": "pipeline",
                            "timestamp": None,
                            "agent": "system",
                            "action": "pipeline_log",
                            "detail": line[:200],
                        })
        except Exception:
            pass

    # 3. Task activity from inbox
    inbox_dir = INBOX
    if inbox_dir.exists():
        task_files = sorted(inbox_dir.glob("task_*.json"), reverse=True)[:10]
        for tf in task_files:
            try:
                task = _read_json(tf)
                if task:
                    activities.append({
                        "source": "tasks",
                        "timestamp": task.get("updated_at"),
                        "agent": task.get("from_agent", "unknown"),
                        "action": f"task_{task.get('status', 'unknown')}",
                        "detail": task.get("title", "")[:200],
                    })
            except Exception:
                pass

    # Sort by timestamp (newest first), None timestamps last
    activities.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

    return {
        "activities": activities[:limit],
        "total": len(activities[:limit]),
        "sources": list(set(a["source"] for a in activities)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Tool: tda_get_campaign_status ───────────────────────────────────────────

async def _mcp_tda_get_campaign_status() -> dict:
    """Return email/EDDM campaign status summary."""
    from datetime import datetime, timezone

    status = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "email_campaigns": [],
        "eddm_campaigns": [],
        "totals": {
            "emails_sent": 0,
            "emails_bounced": 0,
            "delivery_rate": 0,
        },
    }

    # Check bounce database
    bounce_file = BOUNCE_FILE_PATH()
    total_bounces = 0
    if bounce_file.exists():
        try:
            with open(bounce_file) as f:
                total_bounces = sum(1 for line in f if line.strip())
        except Exception:
            pass
    status["totals"]["emails_bounced"] = total_bounces

    # Check outreach directory for campaign files
    outreach_dir = Path("/home/nemesis/.openclaw/workspace/agency/operations/outreach")
    if outreach_dir.exists():
        for f in sorted(outreach_dir.glob("*.md"), reverse=True)[:10]:
            try:
                stat = f.stat()
                status["email_campaigns"].append({
                    "name": f.stem,
                    "path": str(f),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            except Exception:
                pass

    # Check EDDM campaigns directory
    eddm_dir = Path("/home/nemesis/.openclaw/workspace/agency/campaigns")
    if eddm_dir.exists():
        for d in sorted(eddm_dir.iterdir(), reverse=True):
            if d.is_dir():
                try:
                    files = list(d.iterdir())
                    status["eddm_campaigns"].append({
                        "name": d.name,
                        "path": str(d),
                        "files": len(files),
                    })
                except Exception:
                    pass

    # Pipeline status for email metrics
    pipeline_log = Path("/home/nemesis/.openclaw/workspace/agency/operations/pipeline-automation.log")
    if pipeline_log.exists():
        try:
            with open(pipeline_log) as f:
                lines = f.readlines()
                for line in lines[-50:]:
                    line_lower = line.lower()
                    if "sent" in line_lower or "delivered" in line_lower:
                        status["totals"]["emails_sent"] += 1
        except Exception:
            pass

    # Calculate delivery rate
    sent = status["totals"]["emails_sent"]
    bounced = status["totals"]["emails_bounced"]
    if sent > 0:
        status["totals"]["delivery_rate"] = round((sent - bounced) / sent * 100, 1)

    return status


# ── Tool: tda_search_assets ─────────────────────────────────────────────────

async def _mcp_tda_search_assets(
    query: str = "",
    file_type: str = "",
    max_results: int = 20,
) -> dict:
    """Search across all workspace files for assets matching query."""
    from datetime import datetime, timezone

    if not query and not file_type:
        return {"error": "Provide at least a query or file_type", "results": []}

    workspace = Path("/home/nemesis/.openclaw/workspace")
    results = []

    # Define search directories (skip hidden dirs and large binary dirs)
    skip_dirs = {".git", ".clawhub", "__pycache__", "node_modules", ".cache"}
    search_dirs = [
        workspace / "agency",
        workspace / "scripts",
        workspace / "tda-os",
        workspace / "ai-tool-builder",
        workspace / "shared-mcp-server",
    ]

    # File type filter
    type_extensions = {
        "markdown": [".md"],
        "python": [".py"],
        "json": [".json"],
        "csv": [".csv"],
        "text": [".txt", ".log"],
        "html": [".html"],
        "css": [".css"],
        "all": [],
    }

    extensions = type_extensions.get(file_type, []) if file_type else []

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for f in search_dir.rglob("*"):
            if not f.is_file():
                continue
            # Skip hidden/cache dirs
            if any(part in skip_dirs for part in f.parts):
                continue
            # File type filter
            if extensions and f.suffix not in extensions:
                continue
            # Query search
            if query:
                query_lower = query.lower()
                # Match filename
                if query_lower in f.name.lower():
                    pass  # matched
                else:
                    # Try reading file content (skip large files)
                    try:
                        if f.stat().st_size > 1_000_000:  # skip > 1MB
                            continue
                        content = f.read_text(errors="ignore")
                        if query_lower not in content.lower():
                            continue
                    except Exception:
                        continue

            try:
                stat = f.stat()
                results.append({
                    "path": str(f),
                    "name": f.name,
                    "type": f.suffix or "directory",
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            except Exception:
                pass

            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    return {
        "query": query,
        "file_type": file_type,
        "results": results,
        "total": len(results),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── MCP Server Setup ─────────────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools():
    return [
        Tool(
            name="submit_task",
            description="Submit a task to another agent with full context, priority, and expected output. Creates a task in the shared inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short task title"},
                    "description": {"type": "string", "description": "Detailed task description"},
                    "from_agent": {"type": "string", "description": "Agent submitting the task (e.g. 'hermes', 'alex')"},
                    "to_agent": {"type": "string", "description": "Agent to handle the task (e.g. 'hermes', 'alex')"},
                    "priority": {"type": "string", "description": "Priority: urgent, high, medium, low (default: medium)", "default": "medium"},
                    "context": {"type": "string", "description": "Additional context, background info, constraints"},
                    "expected_output": {"type": "string", "description": "What the completed task should produce"},
                    "tags": {"type": "string", "description": "Comma-separated tags (e.g. 'eddm,campaign,analysis')"},
                    "project": {"type": "string", "description": "Project name this task belongs to"},
                },
                "required": ["title", "description", "from_agent", "to_agent"],
            },
        ),
        Tool(
            name="list_tasks",
            description="List all tasks, optionally filtered by status, agent, or project. Sorted by status then priority.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status_filter": {"type": "string", "description": "Filter by status: pending, claimed, in_progress, completed, cancelled"},
                    "agent_filter": {"type": "string", "description": "Filter by agent (matches to_agent or from_agent)"},
                    "project_filter": {"type": "string", "description": "Filter by project name"},
                },
            },
        ),
        Tool(
            name="get_task",
            description="Get full details of a specific task by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID (e.g. 'task_20260523_abc123')"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="claim_task",
            description="Claim a task to prevent other agents from working on it. Only works on pending tasks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to claim"},
                    "agent": {"type": "string", "description": "Agent claiming the task"},
                },
                "required": ["task_id", "agent"],
            },
        ),
        Tool(
            name="update_task",
            description="Update a task's status, add result, notes, or artifact paths.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to update"},
                    "agent": {"type": "string", "description": "Agent making the update"},
                    "status": {"type": "string", "description": "New status: pending, claimed, in_progress, completed, cancelled"},
                    "result": {"type": "string", "description": "Task result or output"},
                    "note": {"type": "string", "description": "Progress note to append"},
                    "add_artifact": {"type": "string", "description": "Path to an artifact file to register"},
                },
                "required": ["task_id", "agent"],
            },
        ),
        Tool(
            name="share_file",
            description="Share a file between agents. Copies to shared outbox with metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the file to share"},
                    "from_agent": {"type": "string", "description": "Agent sharing the file"},
                    "to_agent": {"type": "string", "description": "Target agent (empty for broadcast)"},
                    "description": {"type": "string", "description": "What this file is about"},
                    "task_id": {"type": "string", "description": "Associated task ID if any"},
                },
                "required": ["file_path", "from_agent"],
            },
        ),
        Tool(
            name="list_shared_files",
            description="List all shared files, optionally filtered by target agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_filter": {"type": "string", "description": "Filter by target agent"},
                },
            },
        ),
        Tool(
            name="read_shared_file",
            description="Read a shared file's content and metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "shared_path": {"type": "string", "description": "Path from share_file result"},
                },
                "required": ["shared_path"],
            },
        ),
        Tool(
            name="write_context",
            description="Write to the shared context/knowledge base. Both agents can read/write.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Context key/title (e.g. 'eddm-campaign-status')"},
                    "content": {"type": "string", "description": "Markdown content"},
                    "agent": {"type": "string", "description": "Agent writing the context"},
                    "description": {"type": "string", "description": "Short description of this context entry"},
                    "tags": {"type": "string", "description": "Comma-separated tags for searchability"},
                },
                "required": ["key", "content", "agent"],
            },
        ),
        Tool(
            name="read_context",
            description="Read from the shared context base by key or tag.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Exact context key to read"},
                    "tag": {"type": "string", "description": "Filter by tag"},
                },
            },
        ),
        Tool(
            name="list_context",
            description="List all context entries (metadata only, no content).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="lock_resource",
            description="Lock a resource to prevent concurrent work. Use for files, projects, or any shared resource.",
            inputSchema={
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "Resource identifier (e.g. 'project-eddm-campaign')"},
                    "agent": {"type": "string", "description": "Agent requesting the lock"},
                    "reason": {"type": "string", "description": "Why the lock is needed"},
                },
                "required": ["resource", "agent"],
            },
        ),
        Tool(
            name="unlock_resource",
            description="Release a previously acquired lock.",
            inputSchema={
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "Resource to unlock"},
                    "agent": {"type": "string", "description": "Agent releasing (must match locker)"},
                },
                "required": ["resource", "agent"],
            },
        ),
        Tool(
            name="list_locks",
            description="List all current resource locks.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="workspace_status",
            description="Get full workspace status: task counts, file counts, recent activity, disk usage.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="verify_email",
            description="Verify an email address: MX record check, bounce database lookup, syntax validation, disposable email detection. Returns detailed verification results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Email address to verify"},
                },
                "required": ["email"],
            },
        ),
        Tool(
            name="check_bounce_db",
            description="Check if an email address is in the bounce database without running full verification.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Email address to check"},
                },
                "required": ["email"],
            },
        ),
        Tool(
            name="list_bounces",
            description="List all emails in the bounce database with reasons and dates.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="run_gate",
            description="Run a specific pipeline gate check. Gates: 1=lead_pull, 2=enrichment, 3=compliance, 4=pre_send. Returns pass/fail with issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "gate": {"type": "string", "description": "Gate number: 1, 2, 3, or 4"},
                    "file_path": {"type": "string", "description": "Absolute path to the file to check"},
                },
                "required": ["gate", "file_path"],
            },
        ),
        Tool(
            name="run_all_gates",
            description="Run all pipeline gates (2=enrichment, 3=compliance, 4=pre_send) in sequence. Stops at first failure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the client email file"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="watch_bounces",
            description="Check Gmail inbox for new delivery failure notifications and auto-add to bounce database.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="pipeline_status",
            description="Get pipeline health: sent today, bounced today, pending, bounce DB size, delivery rate.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="verify_prospect_file",
            description="Verify all prospects in a batch file: domain existence, email validity, bounce database check. Returns per-prospect results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the prospect batch Markdown file"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="add_bounce",
            description="Add an email address to the bounce database after it bounces.",
            inputSchema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Email address that bounced"},
                    "reason": {"type": "string", "description": "Why it bounced (e.g. '550 No User', 'Dead domain', 'Delivery failure')"},
                    "action": {"type": "string", "description": "Recommended action (e.g. 'Remove prospect', 'Try info@ domain')"},
                },
                "required": ["email", "reason"],
            },
        ),
        Tool(
            name="log_activity",
            description="Log a custom activity to the audit trail.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action name"},
                    "detail": {"type": "string", "description": "Action details"},
                    "agent": {"type": "string", "description": "Agent performing the action"},
                },
                "required": ["action", "detail", "agent"],
            },
        ),
        Tool(
            name="get_activity_log",
            description="Read the activity log for a given date (default: today).",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format (default: today)"},
                    "limit": {"type": "integer", "description": "Max entries to return (default: 50)", "default": 50},
                },
            },
        ),
        Tool(
            name="cron_status",
            description="Get status of Hermes cron jobs: name, schedule, last run, next run, enabled state. Filter by job name optionally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_name": {"type": "string", "description": "Filter by job name (partial match, optional)"},
                    "show_history": {"type": "boolean", "description": "Include recent run history (default: false)", "default": False},
                },
            },
        ),
        Tool(
            name="backup_status",
            description="Check last backup time, vault sync status, SSD presence, and disk usage.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="stat_lookup",
            description="Search the EDDM data reference for sourced statistics by keyword or category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to search for in stat text (optional)"},
                    "category": {"type": "string", "description": "Filter by section/category name (optional)"},
                    "limit": {"type": "integer", "description": "Max results (default: 10)", "default": 10},
                },
            },
        ),
        Tool(
            name="render_episode",
            description="Render an EDDM episode video from script using FFmpeg pipeline. Async — returns task_id immediately. Default: motion-graphics style.",
            inputSchema={
                "type": "object",
                "properties": {
                    "episode_number": {"type": "integer", "description": "Episode number (1-5)"},
                    "script_path": {"type": "string", "description": "Absolute path to .md script (optional, defaults to standard path)"},
                    "output_path": {"type": "string", "description": "Output .mp4 path (optional)"},
                    "visual_style": {"type": "string", "description": "motion-graphics | ai-video | hybrid (default: motion-graphics)", "default": "motion-graphics"},
                    "voice": {"type": "string", "description": "brian | alex | aria (default: brian)", "default": "brian"},
                },
                "required": ["episode_number"],
            },
        ),
        Tool(
            name="notebooklm_generate",
            description="Create a NotebookLM notebook, upload sources, generate Audio Overview. Fire-and-forget — returns task_id immediately.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_name": {"type": "string", "description": "Name for the notebook"},
                    "sources": {"type": "array", "items": {"type": "string"}, "description": "File paths and/or URLs to upload"},
                    "format": {"type": "string", "description": "BRIEF | DEEP_DIVE (default: DEEP_DIVE)", "default": "DEEP_DIVE"},
                    "length": {"type": "string", "description": "SHORT | LONG (default: LONG)", "default": "LONG"},
                    "output_dir": {"type": "string", "description": "Output directory (optional)"},
                },
                "required": ["notebook_name"],
            },
        ),
        Tool(
            name="deploy_site",
            description="Deploy the Targeted Design Agency site. Requires confirm=True for production deploys. Use dry_run=True to preview.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Deploy commit message (default: 'site update')", "default": "site update"},
                    "confirm": {"type": "boolean", "description": "Required True for production deploy (default: false)", "default": False},
                    "dry_run": {"type": "boolean", "description": "Preview what would be deployed (default: false)", "default": False},
                },
            },
        ),
        Tool(
            name="maton_search",
            description="Search the Lead Pipeline Google Sheet via Maton Google Sheets connection. Query any field (business name, ZIP, category, etc.). Reads MATON_API_KEY from env.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term — business name, ZIP, category, etc. (e.g. 'HVAC', '78237', 'plumber')"},
                    "location": {"type": "string", "description": "City/state/ZIP filter (optional)"},
                    "limit": {"type": "integer", "description": "Max results (default: 20, max: 100)", "default": 20},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="generate_tool_spec",
            description="Generate a custom AI tool specification from client requirements. Takes business type, problem, and data source → returns AI-generated spec with features, pricing, and timeline. Powered by Gemini AI.",
            inputSchema={
                "type": "object",
                "properties": {
                    "business_type": {"type": "string", "description": "Type of business (e.g. 'Restaurant', 'Real Estate', 'Retail Shop', 'Home Services')"},
                    "problem": {"type": "string", "description": "The problem the client wants to solve (e.g. 'I lose track of potential customers')"},
                    "data_source": {"type": "string", "description": "Where their data currently lives (e.g. 'Spreadsheets', 'Paper notes', 'Nothing yet')", "default": ""},
                },
                "required": ["business_type", "problem"],
            },
        ),
        Tool(
            name="get_tool_templates",
            description="Return the AI Tool Builder template library. Lists all 5 tool templates with pricing, features, and hosting costs. Optionally filter by template_id for full details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "Specific template ID (optional). One of: lead_tracker, inventory_manager, feedback_collector, booking_scheduler, dashboard_reports"},
                },
            },
        ),
        Tool(
            name="get_pricing_recommendation",
            description="Get a pricing recommendation based on business type and requirements. Matches client needs to the best tool template and provides customized price estimate.",
            inputSchema={
                "type": "object",
                "properties": {
                    "business_type": {"type": "string", "description": "Type of business (e.g. 'Restaurant', 'Real Estate')"},
                    "requirements": {"type": "string", "description": "What the client needs the tool to do", "default": ""},
                    "budget_hint": {"type": "string", "description": "Budget context (e.g. 'low budget', 'premium', 'flexible')", "default": ""},
                },
                "required": ["business_type"],
            },
        ),
        Tool(
            name="tda_get_dashboard_stats",
            description="TDA OS: Return key dashboard metrics including active clients, pipeline value, emails sent/bounced today, tools built, and MRR.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="tda_get_agent_status",
            description="TDA OS: Return status of all agents based on recent activity logs (active/idle/offline) with recent actions.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="tda_get_pipeline_summary",
            description="TDA OS: Return pipeline grouped by status with counts, values, prospect details, and MRR breakdown.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="tda_get_recent_activity",
            description="TDA OS: Return recent actions across all systems (MCP logs, pipeline logs, tasks). Default 30 entries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max entries to return (default: 30)", "default": 30},
                },
            },
        ),
        Tool(
            name="tda_get_campaign_status",
            description="TDA OS: Return email/EDDM campaign status including sent counts, bounce counts, delivery rate, and campaign files.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="tda_search_assets",
            description="TDA OS: Search across all workspace files by query string and/or file type (markdown, python, json, csv, text, html, css).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term to match in filename or content"},
                    "file_type": {"type": "string", "description": "Filter by file type: markdown, python, json, csv, text, html, css"},
                    "max_results": {"type": "integer", "description": "Max results (default: 20)", "default": 20},
                },
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    try:
        if name == "submit_task":
            result = await _submit_task(**arguments)
        elif name == "list_tasks":
            result = await _list_tasks(**arguments)
        elif name == "get_task":
            result = await _get_task(**arguments)
        elif name == "claim_task":
            result = await _claim_task(**arguments)
        elif name == "update_task":
            result = await _update_task(**arguments)
        elif name == "share_file":
            result = await _share_file(**arguments)
        elif name == "list_shared_files":
            result = await _list_shared_files(**arguments)
        elif name == "read_shared_file":
            result = await _read_shared_file(**arguments)
        elif name == "write_context":
            result = await _write_context(**arguments)
        elif name == "read_context":
            result = await _read_context(**arguments)
        elif name == "list_context":
            result = await _list_context()
        elif name == "lock_resource":
            result = await _lock_resource(**arguments)
        elif name == "unlock_resource":
            result = await _unlock_resource(**arguments)
        elif name == "list_locks":
            result = await _list_locks()
        elif name == "workspace_status":
            result = await _workspace_status()
        elif name == "log_activity":
            result = await _log_activity(**arguments)
        elif name == "get_activity_log":
            result = await _get_activity_log(**arguments)
        elif name == "verify_email":
            result = await _mcp_verify_email(**arguments)
        elif name == "check_bounce_db":
            result = await _mcp_check_bounce_db(**arguments)
        elif name == "list_bounces":
            result = await _mcp_list_bounces()
        elif name == "add_bounce":
            result = await _mcp_add_bounce(**arguments)
        elif name == "verify_prospect_file":
            result = await _mcp_verify_prospect_file(**arguments)
        elif name == "run_gate":
            result = await _mcp_run_gate(**arguments)
        elif name == "run_all_gates":
            result = await _mcp_run_all_gates(**arguments)
        elif name == "watch_bounces":
            result = await _mcp_watch_bounces()
        elif name == "pipeline_status":
            result = await _mcp_pipeline_status()
        elif name == "cron_status":
            job_name = arguments.get("job_name", "")
            show_history = arguments.get("show_history", False)
            result = await _mcp_cron_status(job_name=job_name, show_history=show_history)
        elif name == "backup_status":
            result = await _mcp_backup_status()
        elif name == "stat_lookup":
            query = arguments.get("query", "")
            category = arguments.get("category", "")
            limit = arguments.get("limit", 10)
            result = await _mcp_stat_lookup(query=query, category=category, limit=limit)
        elif name == "render_episode":
            result = await _mcp_render_episode(**arguments)
        elif name == "notebooklm_generate":
            result = await _mcp_notebooklm_generate(**arguments)
        elif name == "deploy_site":
            result = await _mcp_deploy_site(**arguments)
        elif name == "maton_search":
            result = await _mcp_maton_search(**arguments)
        elif name == "generate_tool_spec":
            result = await _mcp_generate_tool_spec(**arguments)
        elif name == "get_tool_templates":
            result = await _mcp_get_tool_templates(**arguments)
        elif name == "get_pricing_recommendation":
            result = await _mcp_get_pricing_recommendation(**arguments)
        elif name == "tda_get_dashboard_stats":
            result = await _mcp_tda_get_dashboard_stats()
        elif name == "tda_get_agent_status":
            result = await _mcp_tda_get_agent_status()
        elif name == "tda_get_pipeline_summary":
            result = await _mcp_tda_get_pipeline_summary()
        elif name == "tda_get_recent_activity":
            limit = arguments.get("limit", 30)
            result = await _mcp_tda_get_recent_activity(limit=limit)
        elif name == "tda_get_campaign_status":
            result = await _mcp_tda_get_campaign_status()
        elif name == "tda_search_assets":
            query = arguments.get("query", "")
            file_type = arguments.get("file_type", "")
            max_results = arguments.get("max_results", 20)
            result = await _mcp_tda_search_assets(query=query, file_type=file_type, max_results=max_results)
        else:
            raise ValueError(f"Unknown tool: {name}")
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        raise


async def _mcp_verify_email(email: str) -> dict:
    """Verify an email address through all checks"""
    if _verify_email_func is None or _load_bounce_db is None:
        return {"error": "Email verification module not available. Check scripts/verify-emails.py exists."}
    bounce_db = _load_bounce_db()
    result = _verify_email_func(email, bounce_db)
    return result

async def _mcp_check_bounce_db(email: str) -> dict:
    """Check if email is in bounce database"""
    if _load_bounce_db is None:
        return {"error": "Bounce database module not available"}
    bounce_db = _load_bounce_db()
    email_lower = email.lower().strip()
    if email_lower in bounce_db:
        return {"bounced": True, "details": bounce_db[email_lower]}
    return {"bounced": False}

async def _mcp_list_bounces() -> list:
    """List all bounced emails"""
    if _load_bounce_db is None:
        return [{"error": "Bounce database module not available"}]
    bounce_db = _load_bounce_db()
    return [{"email": k, **v} for k, v in bounce_db.items()]

async def _mcp_run_gate(gate: str, file_path: str) -> dict:
    """Run a specific pipeline gate (1-4)"""
    import subprocess
    result = subprocess.run(
        ['python3', str(SCRIPTS_DIR / 'pipeline_gates.py'), f'gate{gate}', file_path],
        capture_output=True, text=True, timeout=60
    )
    return {
        'gate': gate,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'exit_code': result.returncode,
        'passed': result.returncode == 0
    }

async def _mcp_run_all_gates(file_path: str) -> dict:
    """Run all pipeline gates (2-4) for a client email file"""
    import subprocess
    result = subprocess.run(
        ['python3', str(SCRIPTS_DIR / 'pipeline_gates.py'), 'all', file_path],
        capture_output=True, text=True, timeout=120
    )
    return {
        'stdout': result.stdout,
        'stderr': result.stderr,
        'exit_code': result.returncode,
        'passed': result.returncode == 0
    }

async def _mcp_watch_bounces() -> dict:
    """Check inbox for new bounces"""
    import subprocess
    result = subprocess.run(
        ['python3', str(SCRIPTS_DIR / 'pipeline_gates.py'), 'watch-bounces'],
        capture_output=True, text=True, timeout=60
    )
    try:
        return json.loads(result.stdout.split('\n')[-1]) if result.stdout.strip() else {'raw': result.stdout}
    except:
        return {'raw': result.stdout, 'stderr': result.stderr}

async def _mcp_pipeline_status() -> dict:
    """Get pipeline health status"""
    import subprocess
    result = subprocess.run(
        ['python3', str(SCRIPTS_DIR / 'pipeline_gates.py'), 'status'],
        capture_output=True, text=True, timeout=30
    )
    try:
        return json.loads(result.stdout.split('\n')[-1]) if result.stdout.strip() else {'raw': result.stdout}
    except:
        return {'raw': result.stdout, 'stderr': result.stderr}

async def _mcp_verify_prospect_file(file_path: str) -> dict:
    """Verify all prospects in a batch file"""
    if _parse_prospect_file is None or _verify_prospect is None:
        return {"error": "Prospect verification module not available"}
    from pathlib import Path as _Path
    batch_file = _Path(file_path)
    if not batch_file.exists():
        return {"error": f"File not found: {file_path}"}
    prospects = _parse_prospect_file(batch_file)
    bounce_db = _load_bounce_db2() if _load_bounce_db2 else {}
    results = [_verify_prospect(p, bounce_db) for p in prospects]
    ok = sum(1 for r in results if r['valid'])
    invalid = sum(1 for r in results if not r['valid'])
    return {
        "file": file_path,
        "total": len(results),
        "valid": ok,
        "invalid": invalid,
        "results": results
    }

async def _mcp_add_bounce(email: str, reason: str, action: str = "") -> dict:
    """Add an email to the bounce database"""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    bounce_file = BOUNCE_FILE_PATH()
    with open(bounce_file, 'a') as f:
        line = f"{email}|{today}|mcp|{reason}|{action}\n"
        f.write(line)
    return {"added": True, "email": email, "date": today}

# ── Tool: cron_status ────────────────────────────────────────────────────────

async def _mcp_cron_status(job_name: str = "", show_history: bool = False) -> dict:
    """Get status of Hermes cron jobs via subprocess call to cronjob list."""
    import subprocess
    from datetime import datetime, timezone
    try:
        result = subprocess.run(
            ['python3', '-c', '''
import sys, json
sys.path.insert(0, "/home/nemesis/.hermes/hermes-agent")
from hermes_tools import cj
r = cj(action="list")
print(json.dumps(r, indent=2, default=str))
'''],
            capture_output=True, text=True, timeout=30,
            cwd="/home/nemesis/.hermes/hermes-agent"
        )
        if result.returncode != 0:
            # Fallback: return raw output
            return {"error": result.stderr[:500], "raw": result.stdout[:500]}
        data = json.loads(result.stdout)
        jobs = data.get("jobs", [])
        if job_name:
            jobs = [j for j in jobs if job_name.lower() in j.get("name", "").lower()]
        return {"jobs": jobs, "total": len(jobs), "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"error": str(e)}

# ── Tool: backup_status ──────────────────────────────────────────────────────

async def _mcp_backup_status() -> dict:
    """Check last backup time, vault sync status, and SSD health."""
    from datetime import datetime, timezone
    import subprocess
    
    status = {"timestamp": datetime.now(timezone.utc).isoformat()}
    
    # Check last backup
    backup_dir = Path("/home/nemesis/shared-mcp-server/backups")
    if backup_dir.exists():
        backups = sorted(backup_dir.glob("*.tar.gz"), reverse=True)
        if backups:
            last = backups[0]
            stat = last.stat()
            status["last_backup"] = {
                "file": str(last),
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                if hasattr(stat, 'st_mtime') else None,
            }
        else:
            status["last_backup"] = None
    else:
        status["last_backup"] = {"error": "Backup directory not found"}
    
    # Check vault sync - look at most recent chat history file
    vault_dir = Path("/home/nemesis/.openclaw/workspace/vault/70-Chat-History")
    if vault_dir.exists():
        vault_files = sorted(vault_dir.glob("*.md"), reverse=True)
        if vault_files:
            latest = vault_files[0]
            stat = latest.stat()
            status["vault_sync"] = {
                "latest_file": str(latest),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            }
        else:
            status["vault_sync"] = {"error": "No chat history files found"}
    else:
        status["vault_sync"] = {"error": "Vault directory not found"}
    
    # Check SSD (sdb) health
    try:
        sdb = Path("/dev/sdb")
        if sdb.exists():
            result = subprocess.run(
                ['lsblk', '-o', 'NAME,SIZE,MOUNTPOINT,FSTYPE', '-n', '-p', '/dev/sdb'],
                capture_output=True, text=True, timeout=10
            )
            status["ssd"] = {"present": True, "info": result.stdout.strip()}
        else:
            status["ssd"] = {"present": False, "info": "/dev/sdb not found"}
    except Exception as e:
        status["ssd"] = {"error": str(e)}
    
    # Disk usage
    try:
        import shutil
        total, used, free = shutil.disk_usage("/home/nemesis")
        status["disk"] = {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "pct_used": round(used / total * 100, 1)
        }
    except Exception as e:
        status["disk"] = {"error": str(e)}
    
    return status

# ── Tool: stat_lookup ────────────────────────────────────────────────────────

async def _mcp_stat_lookup(query: str = "", category: str = "", limit: int = 10) -> dict:
    """Search the EDDM data reference for sourced statistics."""
    ref_path = Path("/home/nemesis/.hermes/content-pipeline/eddm-series/data-reference.md")
    if not ref_path.exists():
        return {"error": "data-reference.md not found", "path": str(ref_path)}
    
    content = ref_path.read_text()
    lines = content.split('\n')
    
    results = []
    current_section = ""
    
    for line in lines:
        if line.startswith('## '):
            current_section = line[3:].strip()
            continue
        
        match = False
        if query and query.lower() in line.lower():
            match = True
        if category and category.lower() in current_section.lower():
            match = True
        
        if match and line.strip() and not line.startswith('#'):
            results.append({
                "section": current_section,
                "text": line.strip()[:300]
            })
    
    # Apply limit
    results = results[:limit]
    
    return {
        "query": query,
        "category": category,
        "results": results,
        "total_matches": len(results),
        "source": str(ref_path)
    }

def BOUNCE_FILE_PATH():
    return Path("/home/nemesis/.openclaw/workspace/agency/operations/blocked-lists/hard-bounces.txt")

# ── Tool: render_episode ─────────────────────────────────────────────────────

async def _mcp_render_episode(
    episode_number: int,
    script_path: str = "",
    output_path: str = "",
    visual_style: str = "motion-graphics",
    voice: str = "brian"
) -> dict:
    """Render an EDDM episode video from script using FFmpeg pipeline. Returns task_id for async tracking."""
    from datetime import datetime, timezone
    import subprocess, os
    
    # Validate episode number
    if episode_number < 1 or episode_number > 5:
        return {"error": "episode_number must be 1-5"}
    
    # Default script path
    if not script_path:
        script_path = f"/home/nemesis/.hermes/content-pipeline/eddm-series/episode-{episode_number:02d}-script.md"
    if not os.path.exists(script_path):
        return {"error": f"Script not found: {script_path}"}
    
    # Default output path
    if not output_path:
        output_path = f"/home/nemesis/.hermes/content-pipeline/output/eddm-ep{episode_number:02d}-mcp.mp4"
    
    # Build FFmpeg command based on visual style
    # For now, use the proven motion-graphics pipeline (v3)
    base = "/home/nemesis/.hermes/content-pipeline/eddm-series"
    assets = os.path.join(base, "assets")
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate audio from script using edge-tts
    voice_map = {
        "brian": "en-US-BrianNeural",
        "alex": "en-US-ChristopherNeural",
        "aria": "en-US-AriaNeural",
    }
    tts_voice = voice_map.get(voice, "en-US-BrianNeural")
    
    # Read script to get VO text
    try:
        with open(script_path) as f:
            script_content = f.read()
    except Exception as e:
        return {"error": f"Failed to read script: {e}"}
    
    # Extract VO text (simplified — between ## VO markers or full script)
    vo_text = script_content
    if "## VO" in script_content:
        vo_text = script_content.split("## VO")[1].split("##")[0].strip()
    
    # Generate audio
    audio_path = os.path.join(base, f"episode-{episode_number:02d}-audio-mcp.wav")
    try:
        tts_result = subprocess.run(
            ["edge-tts", "--voice", tts_voice, "--rate=+10%", "--pitch=-5Hz",
             "--write-media", audio_path, "--text", vo_text[:5000]],
            capture_output=True, text=True, timeout=120
        )
        if tts_result.returncode != 0:
            return {"error": f"TTS failed: {tts_result.stderr[:300]}"}
    except Exception as e:
        return {"error": f"TTS error: {e}"}
    
    # Build FFmpeg render (simplified single-scene render for MCP)
    # Uses the proven v3 pipeline pattern
    W, H, FPS = 1920, 1080, 30
    FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    
    # Get duration from audio
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip())
    except Exception:
        duration = 79.0  # fallback
    
    # Build filter: dark background + title card + audio
    filt = (
        f"color=c=#0a0a1a:s={W}x{H}:d={duration}[bg];"
        f"[bg]drawtext=fontfile={FB}:fontsize=72:fontcolor=#FFD700:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:text='Episode {episode_number}':"
        f"enable='lt(t,3)'[title];"
        f"[title]drawtext=fontfile={FB}:fontsize=36:fontcolor=#FFFFFF:"
        f"x=(w-text_w)/2:y=(h-text_h)/2+100:text='The Targeted Choice':"
        f"enable='lt(t,3)'[out]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-filter_complex", filt,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path
    ]
    
    # Run render in background
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Don't wait — return immediately with task info
        task_id = f"render_ep{episode_number}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        return {
            "task_id": task_id,
            "status": "submitted",
            "episode": episode_number,
            "output_path": output_path,
            "duration_seconds": round(duration, 1),
            "visual_style": visual_style,
            "voice": voice,
            "pid": proc.pid,
            "message": f"Episode {episode_number} render submitted. Check output: {output_path}"
        }
    except Exception as e:
        return {"error": f"Render failed: {e}"}

# ── Tool: notebooklm_generate ────────────────────────────────────────────────

async def _mcp_notebooklm_generate(
    notebook_name: str,
    sources: list = None,
    format: str = "DEEP_DIVE",
    length: str = "LONG",
    output_dir: str = ""
) -> dict:
    """Create a NotebookLM notebook, upload sources, generate Audio Overview. Fire-and-forget — returns task_id."""
    from datetime import datetime, timezone
    import subprocess
    
    if not output_dir:
        output_dir = "/home/nemesis/.hermes/content-pipeline/notebooklm-output"
    os.makedirs(output_dir, exist_ok=True)
    
    task_id = f"notebooklm_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    
    # Build notebooklm CLI command
    cmd_parts = ["notebooklm", "create", "--name", notebook_name, "--format", format, "--length", length]
    
    if sources:
        for src in sources:
            cmd_parts.extend(["--source", src])
    
    cmd_parts.extend(["--output", output_dir])
    
    # Run asynchronously (fire-and-forget)
    try:
        proc = subprocess.Popen(
            cmd_parts, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return {
            "task_id": task_id,
            "status": "submitted",
            "notebook_name": notebook_name,
            "format": format,
            "length": length,
            "sources_count": len(sources) if sources else 0,
            "output_dir": output_dir,
            "pid": proc.pid,
            "message": f"NotebookLM generation submitted for '{notebook_name}'. Poll for completion."
        }
    except Exception as e:
        return {"error": f"NotebookLM error: {e}"}

# ── Tool: deploy_site ────────────────────────────────────────────────────────

async def _mcp_deploy_site(message: str = "site update", confirm: bool = False, dry_run: bool = False) -> dict:
    """Deploy the Targeted Design Agency site. Requires confirm=True for production deploys."""
    from datetime import datetime, timezone
    import subprocess
    
    if not confirm and not dry_run:
        return {
            "error": "Production deploy requires confirm=True. Use dry_run=True to preview.",
            "hint": "Call with confirm=True to actually deploy, or dry_run=True to see what would happen"
        }
    
    deploy_script = "/home/nemesis/targeted-design-site/deploy.sh"
    if not os.path.exists(deploy_script):
        return {"error": f"Deploy script not found: {deploy_script}"}
    
    if dry_run:
        # Just show git status and what would be committed
        try:
            status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=15,
                cwd="/home/nemesis/targeted-design-site"
            )
            return {
                "status": "dry-run",
                "would_commit": message,
                "git_status": status.stdout,
                "deploy_script": deploy_script,
                "dry_run": True
            }
        except Exception as e:
            return {"error": f"Dry run failed: {e}"}
    
    # Production deploy
    try:
        result = subprocess.run(
            ["bash", deploy_script, message],
            capture_output=True, text=True, timeout=120,
            cwd="/home/nemesis/targeted-design-site"
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "message": message,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-500:] if result.stderr else "",
            "exit_code": result.returncode,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "verify_url": "https://targeted-design.com"
        }
    except Exception as e:
        return {"error": f"Deploy failed: {e}"}

# ── Tool: maton_search ───────────────────────────────────────────────────────

async def _mcp_maton_search(
    query: str,
    location: str = "",
    limit: int = 20,
    filters: str = ""
) -> dict:
    """Search the lead pipeline Google Sheet via Maton Google Sheets connection. Reads MATON_API_KEY from env."""
    import aiohttp, urllib.parse
    from datetime import datetime, timezone
    
    maton_key = os.environ.get("MATON_API_KEY", "")
    if not maton_key:
        env_file = "/home/nemesis/.hermes/.env"
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MATON_API_KEY="):
                        maton_key = line.split("=", 1)[1].strip()
                        break
    
    if not maton_key:
        return {"error": "MATON_API_KEY not set. Add it to /home/nemesis/.hermes/.env"}
    
    SHEETS_CONN = "06dd4428-14ed-433f-b279-90f0eea7f764"
    SHEET_ID = "1yGDvkbGUB0wrQ_TkXhNBbFjxZkvhN9MPLceRfmUmKKE"
    
    headers = {
        "Authorization": f"Bearer {maton_key}",
        "Maton-Connection": SHEETS_CONN,
        "Content-Type": "application/json"
    }
    
    try:
        # Read from Lead Pipeline sheet
        range_encoded = urllib.parse.quote("Lead Pipeline!A2:Z1000")
        url = f"https://gateway.maton.ai/google-sheets/v4/spreadsheets/{SHEET_ID}/values/{range_encoded}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rows = data.get("values", [])
                    
                    # Filter by query (search across all columns)
                    results = []
                    for row in rows:
                        row_text = " ".join(str(c) for c in row).lower()
                        if query.lower() in row_text:
                            if location and location.lower() not in row_text:
                                continue
                            results.append({
                                "data": row,
                                "row_index": rows.index(row) + 2  # +2 for header + 0-index
                            })
                            if len(results) >= limit:
                                break
                    
                    return {
                        "results": results,
                        "total": len(results),
                        "query": query,
                        "location": location,
                        "source": "Lead Pipeline Google Sheet",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                else:
                    body = await resp.text()
                    return {"error": f"Maton/Sheets API error {resp.status}: {body[:300]}"}
    except Exception as e:
        return {"error": f"Search failed: {e}"}

# ── Tool: generate_tool_spec ─────────────────────────────────────────────────

async def _mcp_generate_tool_spec(
    business_type: str,
    problem: str,
    data_source: str = "",
) -> dict:
    """Generate a custom AI tool specification based on client requirements.
    
    Takes a business type, problem description, and current data source,
    then uses Gemini AI to generate a complete tool specification including
    features, data fields, AI capabilities, pricing, and timeline.
    """
    import sys as _sys
    _sys.path.insert(0, "/home/nemesis/.openclaw/workspace/ai-tool-builder")
    
    try:
        from utils.gemini_client import GeminiClient
        from utils.templates import TEMPLATES
    except ImportError as e:
        return {"error": f"AI Tool Builder module not available: {e}"}
    
    try:
        client = GeminiClient()
    except ValueError:
        # Gemini not configured — return template-based fallback
        return {
            "status": "template_fallback",
            "message": "Gemini API key not configured. Returning template-based specification.",
            "business_type": business_type,
            "problem": problem,
            "matching_templates": _match_templates(business_type, problem),
            "note": "Set GEMINI_API_KEY for AI-generated specs.",
        }
    
    try:
        spec_text = client.generate_tool_spec(business_type, problem, data_source)
        import json
        try:
            spec = json.loads(spec_text)
        except json.JSONDecodeError:
            spec = {"raw_specification": spec_text}
        
        # Also include matching templates for reference
        spec["_matching_templates"] = _match_templates(business_type, problem)
        spec["_status"] = "ai_generated"
        return spec
    except RuntimeError as e:
        return {"error": f"Gemini API error: {e}", "hint": "Check API key and quota"}


def _match_templates(business_type: str, problem: str) -> list:
    """Match business type and problem to available templates."""
    from utils.templates import TEMPLATES
    
    keywords = {
        "lead_tracker": ["lead", "crm", "customer", "follow", "sales", "pipeline", "prospect", "deal", "track", "buyer", "client"],
        "inventory_manager": ["inventory", "stock", "product", "supply", "warehouse", "reorder", "sku"],
        "feedback_collector": ["feedback", "review", "survey", "nps", "satisfaction", "rating", "complaint"],
        "booking_scheduler": ["booking", "appointment", "schedule", "calendar", "reserve", "availability"],
        "dashboard_reports": ["dashboard", "report", "analytics", "metric", "kpi", "forecast", "insight"],
    }
    
    text = f"{business_type} {problem}".lower()
    scores = {}
    for tool_id, kws in keywords.items():
        score = sum(1 for kw in kws if kw in text)
        if score > 0:
            scores[tool_id] = score
    
    matched = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {"id": tid, "name": TEMPLATES[tid]["name"], "relevance_score": score}
        for tid, score in matched
    ]


# ── Tool: get_tool_templates ────────────────────────────────────────────────

async def _mcp_get_tool_templates(template_id: str = "") -> dict:
    """Return the tool template library with pricing and feature details.
    
    If template_id is provided, returns full details for that specific template.
    Otherwise returns a summary of all 5 available templates.
    """
    import sys as _sys
    _sys.path.insert(0, "/home/nemesis/.openclaw/workspace/ai-tool-builder")
    
    try:
        from utils.templates import TEMPLATES, get_template, list_templates
    except ImportError as e:
        return {"error": f"Template module not available: {e}"}
    
    if template_id:
        tmpl = get_template(template_id)
        if not tmpl:
            return {
                "error": f"Template '{template_id}' not found",
                "available": [k for k in TEMPLATES.keys()],
            }
        return {"template": tmpl, "id": template_id}
    
    # Return all templates with full details
    all_templates = {}
    for tid, tdata in TEMPLATES.items():
        all_templates[tid] = {
            "name": tdata["name"],
            "description": tdata["description"],
            "category": tdata["category"],
            "price_range": tdata["price_range"],
            "monthly_hosting": tdata["monthly"],
            "timeline": tdata["timeline"],
            "feature_count": len(tdata["features"]),
            "ai_feature_count": len(tdata["ai_features"]),
            "best_for": tdata.get("best_for", []),
        }
    
    return {
        "templates": all_templates,
        "total": len(all_templates),
        "agency": "Targeted Design Agency",
        "note": "All prices are for San Antonio small business clients. Custom quotes available.",
    }


# ── Tool: get_pricing_recommendation ────────────────────────────────────────

async def _mcp_get_pricing_recommendation(
    business_type: str,
    requirements: str = "",
    budget_hint: str = "",
) -> dict:
    """Get a pricing recommendation based on business type and requirements.
    
    Analyzes the business type and requirements to recommend the best
    tool template and provide a customized price estimate.
    """
    import sys as _sys
    _sys.path.insert(0, "/home/nemesis/.openclaw/workspace/ai-tool-builder")
    
    try:
        from utils.templates import TEMPLATES
    except ImportError as e:
        return {"error": f"Template module not available: {e}"}
    
    # Match to templates
    matches = _match_templates(business_type, f"{requirements} {budget_hint}")
    
    if not matches:
        # General recommendation
        return {
            "business_type": business_type,
            "recommendation": "general",
            "message": "Based on your business type, we recommend starting with a consultation.",
            "available_tools": [
                {"id": k, "name": v["name"], "price": v["price_range"], "monthly": v["monthly"]}
                for k, v in TEMPLATES.items()
            ],
            "next_step": "Contact Targeted Design Agency for a free consultation.",
        }
    
    # Build recommendation from top match
    top_id = matches[0]["id"]
    top_template = TEMPLATES[top_id]
    
    # Parse price range for estimate
    price_str = top_template["price_range"]
    
    recommendation = {
        "business_type": business_type,
        "recommended_tool": {
            "id": top_id,
            "name": top_template["name"],
            "description": top_template["description"],
        },
        "pricing": {
            "build_price_range": price_str,
            "monthly_hosting": top_template["monthly"],
            "estimated_timeline": top_template["timeline"],
        },
        "key_features": top_template["features"][:5],
        "ai_capabilities": top_template["ai_features"],
        "best_for_match": business_type in str(top_template.get("best_for", [])),
        "alternative_tools": [
            {"id": m["id"], "name": m["name"], "relevance": m["relevance_score"]}
            for m in matches[1:3]
        ],
        "agency": "Targeted Design Agency",
        "location": "San Antonio, TX",
        "next_step": "Schedule a free consultation to finalize scope and get an exact quote.",
    }
    
    # Budget-aware guidance
    if budget_hint:
        budget_lower = budget_hint.lower()
        if any(w in budget_lower for w in ["low", "cheap", "basic", "minimal", "tight"]):
            recommendation["budget_note"] = "Consider starting with the Lead Tracker / CRM — lowest entry price with highest ROI for most small businesses."
        elif any(w in budget_lower for w in ["premium", "full", "complete", "enterprise", "unlimited"]):
            recommendation["budget_note"] = "Consider bundling multiple tools for a package discount. The Business Dashboard pairs well with any operational tool."
        else:
            recommendation["budget_note"] = "We offer flexible payment plans. Ask about our 50/50 split (half upfront, half on delivery)."
    
    return recommendation


async def main():
    print("DEBUG: Shared MCP Server starting", file=sys.stderr)
    while True:
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        except ValueError as e:
            if "I/O operation on closed file" in str(e):
                await asyncio.sleep(1)
                continue
            raise
        except Exception as e:
            print(f"DEBUG: main loop error: {e}", file=sys.stderr)
            await asyncio.sleep(5)

# Suppress exit method not implemented warning from MCP SDK
import logging
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.CRITICAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("DEBUG: Interrupted", file=sys.stderr)

# SHARED PROTOCOL.md
# Hermes ↔ Alex Sterling Collaboration Protocol v1.0

## Workspace Structure

```
/home/nemesis/shared-mcp-server/
├── workspace/
│   ├── inbox/          # Task files (task_*.json + task_*.md)
│   ├── outbox/         # Shared files with metadata (.meta.json pairs)
│   ├── projects/       # Project-specific working files
│   ├── context/        # Shared knowledge base (key.md + key.meta.json pairs)
│   └── logs/           # Daily activity logs (YYYY-MM-DD.jsonl)
├── locks/              # Resource lock files (*.lock)
├── backups/            # Daily workspace snapshots (*.tar.gz)
└── server.py           # MCP server
```

## MCP Server Tools

### Task Management
| Tool | Purpose |
|------|---------|
| `submit_task` | Create a new task with full context, priority, expected output |
| `list_tasks` | List tasks by status, agent, or project |
| `get_task` | Get full task details |
| `claim_task` | Lock a task for exclusive work |
| `update_task` | Update status, add result/notes/artifacts |

### File Exchange
| Tool | Purpose |
|------|---------|
| `share_file` | Copy a file to outbox with metadata |
| `list_shared_files` | List available shared files |
| `read_shared_file` | Read shared file content + metadata |

### Context / Knowledge Base
| Tool | Purpose |
|------|---------|
| `write_context` | Write to shared knowledge base |
| `read_context` | Read by key or tag |
| `list_context` | List all context entries |

### Resource Locking
| Tool | Purpose |
|------|---------|
| `lock_resource` | Prevent concurrent work on a resource |
| `unlock_resource` | Release a lock |
| `list_locks` | Show all current locks |

### Monitoring
| Tool | Purpose |
|------|---------|
| `workspace_status` | Task counts, file counts, disk, recent activity |
| `log_activity` | Write custom audit entry |
| `get_activity_log` | Read audit trail for a date |

## Task Lifecycle

```
pending → claimed → in_progress → completed
                  ↘ cancelled
```

1. **Submit**: `submit_task` with title, description, from_agent, to_agent, priority, context, expected_output
2. **Claim**: `claim_task` — recipient claims it (prevents duplicate work)
3. **Update**: `update_task` with status=in_progress, add progress notes
4. **Complete**: `update_task` with status=completed, result, and artifact paths
5. **Archive**: Completed tasks stay in inbox; backup includes them

## Agent Registration

- **Hermes** (OWL): `agent="hermes"` — technical ops, automation, MCP infra
- **Alex Sterling**: `agent="alex"` — strategy, analysis, code review

## Communication Rules

1. **All agent-to-agent tasks go through the MCP server** — not direct Discord messages
2. **Discord #executive** is for notifications and human visibility only
3. **Claim before working** — always claim a task before starting
4. **Artifacts in outbox** — completed work products go via `share_file`
5. **Context base** — use `write_context` for persistent shared knowledge (project status, decisions, learnings)
6. **Lock shared resources** — use `lock_resource` before editing shared files

## Priority Levels

| Level | Meaning |
|-------|---------|
| urgent | Drop everything, handle immediately |
| high | Handle today |
| medium | Handle within 2-3 days |
| Low | Handle when convenient |

## Backup

- Daily at 02:00 CST via cron
- 30-day retention
- Backups stored in `backups/` as tar.gz
- Notification posted to Discord #executive on completion

## Connecting as MCP Client

Both Hermes and Alex add this server to their MCP config:

```yaml
mcp_servers:
  shared-bridge:
    command: python3
    args: ["/home/nemesis/shared-mcp-server/server.py"]
    cwd: "/home/nemesis/shared-mcp-server"
```

Or via the wrapper:
```yaml
mcp_servers:
  shared-bridge:
    command: bash
    args: ["/home/nemesis/shared-mcp-server/run.sh"]
    cwd: "/home/nemesis/shared-mcp-server"
```

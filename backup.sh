#!/usr/bin/env bash
# Daily backup of shared workspace
# Run via cron at 2:00 AM CST (07:00 UTC)

BACKUP_DIR="/home/nemesis/shared-mcp-server/backups"
WORKSPACE="/home/nemesis/shared-mcp-server/workspace"
DATE=$(date +%Y-%m-%d)
KEEP_DAYS=30

# Create backup
tar -czf "$BACKUP_DIR/workspace_$DATE.tar.gz" -C "$WORKSPACE" .
echo "[$(date)] Backup created: workspace_$DATE.tar.gz ($(du -h "$BACKUP_DIR/workspace_$DATE.tar.gz" | cut -f1))" >> /tmp/shared_mcp_backup.log

# Remove backups older than KEEP_DAYS
find "$BACKUP_DIR" -name "workspace_*.tar.gz" -mtime "+$KEEP_DAYS" -delete

# Notify via Discord (optional - requires DISCORD_BOT_TOKEN)
if [ -n "$DISCORD_BOT_TOKEN" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_DIR/workspace_$DATE.tar.gz" | cut -f1)
    curl -s -X POST \
        -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"content\":\"📦 Daily workspace backup complete: workspace_$DATE.tar.gz ($BACKUP_SIZE)\"}" \
        "https://discord.com/api/v10/channels/859574313548120066/messages" \
        >> /tmp/shared_mcp_backup.log 2>&1
fi

#!/usr/bin/env bash
# Wrapper for shared-mcp-server - ensures workspace exists and runs MCP server
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure workspace directories exist
for d in workspace/{inbox,outbox,projects,context,logs} backups locks; do
    mkdir -p "$DIR/$d"
done

# Source Hermes env for any needed variables
ENV_FILE="/home/nemesis/.hermes/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Run the server in a restart loop
while true; do
    echo "[$(date)] Starting shared MCP server" >> /tmp/shared_mcp_server.log 2>&1
    python3 "$DIR/server.py" 2>> /tmp/shared_mcp_server.log
    EXIT_CODE=$?
    echo "[$(date)] Shared MCP server exited with code: $EXIT_CODE" >> /tmp/shared_mcp_server.log 2>&1
    sleep 0.5
done

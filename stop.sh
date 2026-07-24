#!/bin/bash
# AITC — Stop all services
LOG_DIR="$HOME/AITC/logs"

stop_pid() {
    local pidfile=$1 label=$2
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 $pid 2>/dev/null; then
            kill $pid && echo "✅ Stopped $label (pid $pid)"
        else
            echo "⚠️  $label not running"
        fi
        rm -f "$pidfile"
    fi
}

stop_pid "$LOG_DIR/fastapi.pid"  "FastAPI backend"
stop_pid "$LOG_DIR/mcp.pid"      "MCP server"
stop_pid "$LOG_DIR/frontend.pid" "Next.js frontend"
stop_pid "$LOG_DIR/mengzi.pid"   "Mengzi classifier"

# Force kill anything on our ports
for port in 3000 3001 3002 8091; do
    fuser -k $port/tcp 2>/dev/null && echo "✅ Freed port $port"
done

echo "All services stopped."

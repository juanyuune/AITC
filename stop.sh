#!/bin/bash
# AITC — Stop all services cleanly
LOG_DIR="$HOME/AITC/logs"

stop_pid() {
    local pidfile=$1 label=$2
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 $pid 2>/dev/null; then
            kill $pid && echo "Stopped $label (pid $pid)"
            sleep 1
        else
            echo "Not running: $label (stale pid $pid)"
        fi
        rm -f "$pidfile"
    else
        echo "No pid file: $label"
    fi
}

echo "Stopping AITC services..."
stop_pid "$LOG_DIR/fastapi.pid"    "FastAPI backend"
stop_pid "$LOG_DIR/mcp.pid"        "MCP server"
stop_pid "$LOG_DIR/frontend.pid"   "Next.js frontend"
stop_pid "$LOG_DIR/bge_router.pid" "BGE-M3 router"
stop_pid "$LOG_DIR/mengzi.pid"     "Mengzi classifier"
stop_pid "$LOG_DIR/qwen3b.pid"     "Qwen 2.5-3B"
stop_pid "$LOG_DIR/qwen.pid"       "Qwen 2.5-14B"

echo "Freeing ports..."
for port in 3000 3001 3002 8000 8001 8091; do
    fuser -k ${port}/tcp 2>/dev/null && echo "Freed port $port"
done

echo "All services stopped."

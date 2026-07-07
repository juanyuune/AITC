#!/bin/bash
# =============================================================================
# start_aitc.sh  —  AITC Credit Investigation Platform — Production Startup
# =============================================================================
# Starts all 4 services in correct dependency order:
#   1. FastAPI backend (port 3001) — must be up before frontend connects
#   2. MCP server    (port 8091) — independent, Claude plugin bridge
#   3. Next.js frontend (port 3000) — depends on backend
#   Note: vLLM (port 8000) is managed separately by root — not started here
#
# Usage:
#   bash ~/AITC/scripts/start_aitc.sh          # start all
#   bash ~/AITC/scripts/start_aitc.sh status   # check status
#   bash ~/AITC/scripts/start_aitc.sh stop     # stop all

AITC_DIR="/home/user/AITC"
AGENT_DIR="/home/user/AITC-CreditInvestigationChatBotAgent"
FRONTEND_DIR="/home/user/AITC-CreditInvestigationChatBotWebUI"
MCP_DIR="$AITC_DIR/mcp-server"
LOG_DIR="$AITC_DIR/logs"
AGENT_VENV="$AITC_DIR/agent-env"
MCP_VENV="/home/user/mcp-venv"
DB_PATH="$AITC_DIR/FinancialStatementXBRL.db"
PID_FILE="$AITC_DIR/.aitc_pids"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }
ok()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ $1"; }
err() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ $1"; }

wait_for_port() {
    local port=$1 name=$2 timeout=${3:-30}
    log "Waiting for $name on port $port..."
    for i in $(seq 1 $timeout); do
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            ok "$name is up on port $port"
            return 0
        fi
        sleep 1
    done
    err "$name failed to start on port $port after ${timeout}s"
    return 1
}

# ── STATUS ──────────────────────────────────────────────────────────────────
status() {
    echo ""
    echo "═══════════════════════════════════════"
    echo "  AITC Service Status"
    echo "═══════════════════════════════════════"
    for port_name in "3001:FastAPI Backend" "3000:Next.js Frontend" "8091:MCP Server" "8000:vLLM (Qwen)"; do
        port="${port_name%%:*}"
        name="${port_name##*:}"
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
            echo "  ✅ $name — Port $port (PID $pid)"
        else
            echo "  ❌ $name — Port $port (not running)"
        fi
    done
    echo ""
    sqlite3 "$DB_PATH" "SELECT COUNT(*) || ' rows in database' FROM financial_metric_value;" 2>/dev/null
    echo "═══════════════════════════════════════"
    echo ""
}

# ── STOP ────────────────────────────────────────────────────────────────────
stop_all() {
    log "Stopping AITC services..."
    pkill -f "python app.py" 2>/dev/null && log "FastAPI backend stopped"
    pkill -f "mcp-server/server.py" 2>/dev/null && log "MCP server stopped"
    pkill -f "next dev\|next start" 2>/dev/null && log "Next.js frontend stopped"
    rm -f "$PID_FILE"
    sleep 2
    status
}

# ── START ────────────────────────────────────────────────────────────────────
start_all() {
    log "Starting AITC Credit Investigation Platform..."
    echo ""

    # Check DB exists
    if [ ! -f "$DB_PATH" ]; then
        err "Database not found: $DB_PATH"
        exit 1
    fi

    # Check vLLM
    if ss -tlnp 2>/dev/null | grep -q ":8000 "; then
        ok "vLLM already running on port 8000"
    else
        err "vLLM not running on port 8000 — start it with root before running this script"
        err "Command: python3 -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-14B-Instruct-AWQ --trust-remote-code --gpu-memory-utilization 0.50 --max-model-len 16384 --enable-auto-tool-choice --tool-call-parser hermes"
    fi

    # ── 1. FastAPI backend ──────────────────────────────────────────────────
    if ss -tlnp 2>/dev/null | grep -q ":3001 "; then
        ok "FastAPI backend already running on port 3001"
    else
        log "Starting FastAPI backend..."
        cd "$AGENT_DIR"
        nohup "$AGENT_VENV/bin/python" app.py >> "$LOG_DIR/backend.log" 2>&1 &
        echo $! >> "$PID_FILE"
        wait_for_port 3001 "FastAPI backend" 30 || exit 1
    fi

    # ── 2. MCP server ───────────────────────────────────────────────────────
    if ss -tlnp 2>/dev/null | grep -q ":8091 "; then
        ok "MCP server already running on port 8091"
    else
        log "Starting MCP server..."
        cd "$MCP_DIR"
        XBRL_DB_PATH="$DB_PATH" nohup "$MCP_VENV/bin/python" server.py >> "$LOG_DIR/mcp.log" 2>&1 &
        echo $! >> "$PID_FILE"
        wait_for_port 8091 "MCP server" 20 || exit 1
    fi

    # ── 3. Next.js frontend ─────────────────────────────────────────────────
    if ss -tlnp 2>/dev/null | grep -q ":3000 "; then
        ok "Next.js frontend already running on port 3000"
    else
        log "Starting Next.js frontend..."
        cd "$FRONTEND_DIR"
        nohup npm run dev >> "$LOG_DIR/frontend.log" 2>&1 &
        echo $! >> "$PID_FILE"
        wait_for_port 3000 "Next.js frontend" 30 || exit 1
    fi

    echo ""
    ok "All services started successfully"
    echo ""
    status
    echo ""
    echo "  Web chat:    http://192.168.20.169:3000"
    echo "  API:         http://192.168.20.169:3001"
    echo "  MCP server:  http://192.168.20.169:8091/sse"
    echo ""
}

# ── MAIN ─────────────────────────────────────────────────────────────────────
case "${1:-start}" in
    start)  start_all ;;
    stop)   stop_all ;;
    status) status ;;
    restart) stop_all; sleep 2; start_all ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac

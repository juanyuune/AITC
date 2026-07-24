#!/bin/bash
# ─────────────────────────────────────────────────────────────
# AITC Credit Investigation Platform — Startup Script
# Updated: 25 July 2026
# Services: MCP Server · FastAPI Backend · Next.js Frontend
# Usage: bash ~/AITC/start.sh
# ─────────────────────────────────────────────────────────────

PROJECT_DIR="$HOME/AITC"
AGENT_DIR="$HOME/AITC-CreditInvestigationChatBotAgent"
WEBUI_DIR="$HOME/AITC-CreditInvestigationChatBotWebUI"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[AITC]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC}  $1"; }

# ── Load env ──────────────────────────────────────────────────
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a && source "$PROJECT_DIR/.env" && set +a
fi

XBRL_DB_PATH="${XBRL_DB_PATH:-$PROJECT_DIR/FinancialStatementXBRL.db}"
XBRL_PORT="${XBRL_PORT:-8091}"

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AITC Credit Investigation Platform — Starting Services  ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Helper: wait_for_port ─────────────────────────────────────
wait_for_port() {
    local port=$1 label=$2 retries=${3:-10}
    for i in $(seq 1 $retries); do
        if curl -s --max-time 1 http://localhost:$port/ &>/dev/null ||            ss -tlnp | grep -q ":$port "; then
            ok "$label is up on port $port"
            return 0
        fi
        sleep 1
    done
    err "$label failed to start on port $port — check $LOG_DIR/"
    return 1
}

# ── Pre-flight ────────────────────────────────────────────────
log "Pre-flight checks..."
[ ! -f "$XBRL_DB_PATH" ] && { err "Database not found: $XBRL_DB_PATH"; exit 1; }
[ ! -f "$AGENT_DIR/app.py" ] && { err "app.py not found: $AGENT_DIR/app.py"; exit 1; }
ok "Database: $XBRL_DB_PATH"
ok "Backend:  $AGENT_DIR/app.py"
echo ""

# ── Service 1: MCP Server (port 8091) ────────────────────────
log "Service 1: XBRL MCP Server (port $XBRL_PORT)..."
if lsof -i :$XBRL_PORT &>/dev/null; then
    ok "MCP server already running on port $XBRL_PORT — skipping"
else
    source "$HOME/mcp-venv/bin/activate"
    XBRL_DB_PATH="$XBRL_DB_PATH" XBRL_PORT="$XBRL_PORT" \
    nohup python "$PROJECT_DIR/mcp-server/server.py" \
        > "$LOG_DIR/mcp-server.log" 2>&1 &
    echo $! > "$LOG_DIR/mcp.pid"
    deactivate 2>/dev/null || true
    wait_for_port $XBRL_PORT "MCP server" 15
fi
echo ""

# ── Service 2: FastAPI Backend (port 3001) ───────────────────
log "Service 2: FastAPI backend (port 3001)..."
if lsof -i :3001 &>/dev/null; then
    ok "FastAPI already running on port 3001 — skipping"
else
    cd "$AGENT_DIR"
    nohup uvicorn app:app \
        --host 0.0.0.0 \
        --port 3001 \
        --workers 1 \
        --log-level info \
        > "$LOG_DIR/fastapi.log" 2>&1 &
    echo $! > "$LOG_DIR/fastapi.pid"
    cd "$PROJECT_DIR"
    wait_for_port 3001 "FastAPI backend" 15
fi
echo ""

# ── Service 3: Next.js Frontend (port 3000) ──────────────────
log "Service 3: Next.js frontend (port 3000)..."
if lsof -i :3000 &>/dev/null; then
    ok "Frontend already running on port 3000 — skipping"
else
    if [ -d "$WEBUI_DIR" ]; then
        cd "$WEBUI_DIR"
        nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
        echo $! > "$LOG_DIR/frontend.pid"
        cd "$PROJECT_DIR"
        wait_for_port 3000 "Next.js frontend" 45
    else
        warn "WebUI dir not found: $WEBUI_DIR — skipping frontend"
    fi
fi
echo ""

# ── Service 4: Mengzi Classifier (port 3002) ─────────────────
log "Service 4: Mengzi classifier (port 3002)..."
if lsof -i :3002 &>/dev/null; then
    ok "Mengzi already running on port 3002 — skipping"
else
    MENGZI_DIR=$(find "$HOME/AITC" -name "mengzi_server.py" 2>/dev/null | head -1)
    if [ -n "$MENGZI_DIR" ]; then
        cd "$(dirname $MENGZI_DIR)"
        nohup python mengzi_server.py > "$LOG_DIR/mengzi.log" 2>&1 &
        echo $! > "$LOG_DIR/mengzi.pid"
        cd "$PROJECT_DIR"
        wait_for_port 3002 "Mengzi classifier" 20
    else
        warn "mengzi_server.py not found — skipping"
    fi
fi
echo ""

# ── Qwen/Fin-R1 (blocked by memory) ──────────────────────────
log "Checking GPU memory for Qwen2.5-14B / Fin-R1 AWQ..."
ROOT_MEM=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null | grep -v "^$" | awk '{sum+=$2} END{print sum}')
if [ -z "$ROOT_MEM" ]; then
    warn "nvidia-smi not available or no GPU processes"
elif [ "$ROOT_MEM" -gt 60000 ]; then
    warn "GPU memory usage ~${ROOT_MEM}MiB — insufficient for Qwen/Fin-R1 (need ~15GiB free)"
    warn "Kill root Qwen3-30B (pid 3329722) and re-run start.sh to activate these models"
else
    ok "GPU memory available — consider starting Qwen2.5-14B on port 8000"
fi
echo ""

# ── Health check ──────────────────────────────────────────────
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Service Status${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

check_port() {
    local port=$1 label=$2
    if curl -s --max-time 1 http://localhost:$port/ &>/dev/null ||        ss -tlnp | grep -q ":$port "; then
        echo -e "  ${GREEN}✅${NC} $label → http://192.168.20.169:$port"
    else
        echo -e "  ${RED}❌${NC} $label → port $port not responding"
    fi
}

check_port 3000 "Next.js Frontend    "
check_port 3001 "FastAPI Backend     "
check_port 3002 "Mengzi Classifier   "
check_port $XBRL_PORT "MCP Server (XBRL)  "
check_port 8000 "Qwen2.5-14B (vLLM) "
check_port 8004 "Fin-R1 AWQ (vLLM)  "

echo ""
echo -e "  Logs  →  ${BLUE}$LOG_DIR/${NC}"
echo ""

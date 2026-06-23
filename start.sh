#!/bin/bash
# ─────────────────────────────────────────────────────────────
# AITC Credit Investigation Chatbot — Startup Script
# Starts all 4 services in the correct order.
# Usage: ./start.sh
# ─────────────────────────────────────────────────────────────

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# ── Colors ───────────────────────────────────────────────────
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${BLUE}[AITC]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC}  $1"; }

# ── Load .env ─────────────────────────────────────────────────
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a && source "$PROJECT_DIR/.env" && set +a
    ok "Loaded .env"
else
    warn ".env not found — using defaults. Copy .env.example to .env to configure."
fi

# ── Defaults ──────────────────────────────────────────────────
XBRL_DB_PATH="${XBRL_DB_PATH:-$PROJECT_DIR/FinancialStatementXBRL.db}"
XBRL_PORT="${XBRL_PORT:-8091}"
VLLM_MODEL="${VLLM_MODEL:-/home/user/models/breeze2-8b}"

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AITC Credit Investigation Chatbot — Starting Services  ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────
log "Running pre-flight checks..."

if [ ! -f "$XBRL_DB_PATH" ]; then
    err "XBRL database not found: $XBRL_DB_PATH"
    err "Set XBRL_DB_PATH in .env and try again."
    exit 1
fi
ok "Database found: $XBRL_DB_PATH"

if [ ! -f "$PROJECT_DIR/app.py" ]; then
    err "app.py not found in $PROJECT_DIR"
    exit 1
fi
ok "app.py found"

if ! command -v node &> /dev/null; then
    warn "node not found — frontend (yarn dev) will be skipped"
    SKIP_FRONTEND=true
fi

echo ""

# ── Service 1: vLLM Breeze2-8B ───────────────────────────────
log "Starting Service 1: vLLM Breeze2-8B (port 8080)..."

if lsof -i :8080 &>/dev/null; then
    ok "vLLM already running on port 8080 — skipping"
else
    if [ -f "$HOME/vllm-install/.vllm/bin/activate" ]; then
        (
            source "$HOME/vllm-install/.vllm/bin/activate"
            export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
            export CUDA_HOME=/usr/local/cuda-13.0
            python -m vllm.entrypoints.openai.api_server \
                --model "$VLLM_MODEL" \
                --host 0.0.0.0 --port 8080 \
                --trust-remote-code --dtype bfloat16 \
                --max-model-len 32768 --gpu-memory-utilization 0.85 \
                > "$LOG_DIR/vllm.log" 2>&1
        ) &
        VLLM_PID=$!
        log "vLLM starting (PID $VLLM_PID) — waiting 30s for model to load..."
        sleep 30
        if kill -0 $VLLM_PID 2>/dev/null; then
            ok "vLLM running (PID $VLLM_PID) → log: logs/vllm.log"
        else
            err "vLLM failed to start — check logs/vllm.log"
            exit 1
        fi
    else
        warn "vLLM venv not found at ~/vllm-install/.vllm — skipping model server"
        warn "The chatbot backend will fail if vLLM is not running separately."
    fi
fi

# ── Service 2: MCP Server ────────────────────────────────────
log "Starting Service 2: XBRL MCP Server (port $XBRL_PORT)..."

if lsof -i :$XBRL_PORT &>/dev/null; then
    ok "MCP server already running on port $XBRL_PORT — skipping"
else
    if [ -f "$HOME/mcp-venv/bin/activate" ]; then
        (
            source "$HOME/mcp-venv/bin/activate"
            export XBRL_DB_PATH="$XBRL_DB_PATH"
            export XBRL_PORT="$XBRL_PORT"
            python "$PROJECT_DIR/mcp-server/server.py" \
                > "$LOG_DIR/mcp-server.log" 2>&1
        ) &
        MCP_PID=$!
        sleep 3
        if kill -0 $MCP_PID 2>/dev/null; then
            ok "MCP server running (PID $MCP_PID) → log: logs/mcp-server.log"
        else
            err "MCP server failed to start — check logs/mcp-server.log"
        fi
    else
        warn "mcp-venv not found — MCP server skipped"
        warn "Run: python3 -m venv ~/mcp-venv && source ~/mcp-venv/bin/activate && pip install -r mcp-server/requirements.txt"
    fi
fi

# ── Service 3: FastAPI Backend ───────────────────────────────
log "Starting Service 3: FastAPI backend (port 3001)..."

if lsof -i :3001 &>/dev/null; then
    ok "Backend already running on port 3001 — skipping"
else
    (
        cd "$PROJECT_DIR"
        python app.py > "$LOG_DIR/backend.log" 2>&1
    ) &
    BACKEND_PID=$!
    sleep 3
    if kill -0 $BACKEND_PID 2>/dev/null; then
        ok "Backend running (PID $BACKEND_PID) → log: logs/backend.log"
    else
        err "Backend failed to start — check logs/backend.log"
        exit 1
    fi
fi

# ── Service 4: React Frontend ────────────────────────────────
if [ "$SKIP_FRONTEND" != "true" ]; then
    log "Starting Service 4: React frontend (port 3000)..."

    if lsof -i :3000 &>/dev/null; then
        ok "Frontend already running on port 3000 — skipping"
    else
        (
            cd "$PROJECT_DIR"
            yarn dev > "$LOG_DIR/frontend.log" 2>&1
        ) &
        FRONTEND_PID=$!
        sleep 5
        if kill -0 $FRONTEND_PID 2>/dev/null; then
            ok "Frontend running (PID $FRONTEND_PID) → log: logs/frontend.log"
        else
            err "Frontend failed to start — check logs/frontend.log"
        fi
    fi
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  All services started${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Chatbot UI  →  ${BLUE}http://192.168.20.169:3000${NC}"
echo -e "  API         →  ${BLUE}http://192.168.20.169:3001${NC}"
echo -e "  MCP server  →  ${BLUE}http://192.168.20.169:$XBRL_PORT/sse${NC}"
echo -e "  vLLM        →  ${BLUE}http://192.168.20.169:8080${NC}"
echo ""
echo -e "  Logs        →  ${BLUE}$LOG_DIR/${NC}"
echo ""
echo -e "  Claude Code plugin:"
echo -e "  ${BLUE}cd plugins/aitc-credit-investigation && claude${NC}"
echo ""

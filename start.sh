#!/bin/bash
# ─────────────────────────────────────────────────────────────
# AITC Credit Investigation Platform — Startup Script
# Updated: August 2026
# Architecture: Non-blocking launch — vLLM models load async
# Services become available progressively — no hard waits
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

QWEN_SNAPSHOT="/home/user/.cache/huggingface/hub/models--Qwen--Qwen2.5-14B-Instruct-AWQ/snapshots/539535859b135b0244c91f3e59816150c8056698/"
QWEN3B_SNAPSHOT="/home/user/.cache/huggingface/hub/models--Qwen--Qwen2.5-3B-Instruct-AWQ/snapshots/3559b226e8ce77211e2c1bd7ddfb7686fec4d6dd/"
VLLM_PYTHON="/home/user/vllm-install/.vllm/bin/python3"

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AITC Credit Investigation Platform — Starting Services  ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Helper: wait for lightweight services only (not vLLM) ─────
wait_for_port() {
    local port=$1 label=$2 retries=${3:-15}
    for i in $(seq 1 $retries); do
        if ss -tlnp | grep -q ":$port "; then
            ok "$label is up on port $port"
            return 0
        fi
        sleep 1
    done
    err "$label failed to start on port $port — check $LOG_DIR/"
    return 1
}

# ── Helper: launch vLLM in background — no blocking wait ──────
launch_vllm() {
    local port=$1 label=$2 model=$3 gpu_util=$4 max_len=$5 logfile=$6 pidfile=$7 extra_flags=${8:-}
    if ss -tlnp | grep -q ":$port "; then
        ok "$label already running on port $port — skipping"
        return 0
    fi
    log "$label launching in background on port $port..."
    VLLM_USE_FLASHINFER_SAMPLER=0 nohup $VLLM_PYTHON \
        -m vllm.entrypoints.openai.api_server \
        --model "$model" \
        --host 0.0.0.0 --port $port \
        --quantization awq \
        --gpu-memory-utilization $gpu_util \
        --max-model-len $max_len \
        $extra_flags \
        > "$LOG_DIR/$logfile" 2>&1 &
    echo $! > "$LOG_DIR/$pidfile"
    ok "$label process started (PID $!) — loading in background"
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
if ss -tlnp | grep -q ":$XBRL_PORT "; then
    ok "MCP server already running — skipping"
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
if ss -tlnp | grep -q ":3001 "; then
    ok "FastAPI already running — skipping"
else
    cd "$AGENT_DIR"
    nohup /home/user/mcp-venv/bin/uvicorn app:app \
        --host 0.0.0.0 --port 3001 \
        --workers 1 --log-level info \
        > "$LOG_DIR/fastapi.log" 2>&1 &
    echo $! > "$LOG_DIR/fastapi.pid"
    cd "$PROJECT_DIR"
    wait_for_port 3001 "FastAPI backend" 15
fi
echo ""

# ── Service 3: Next.js Frontend (port 3000) ──────────────────
log "Service 3: Next.js frontend (port 3000)..."
if ss -tlnp | grep -q ":3000 "; then
    ok "Frontend already running — skipping"
else
    cd "$WEBUI_DIR"
    nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "$LOG_DIR/frontend.pid"
    cd "$PROJECT_DIR"
    wait_for_port 3000 "Next.js frontend" 45
fi
echo ""

# ── Service 4: BGE-M3 Semantic Router (port 3002) ───────────
log "Service 4: BGE-M3 semantic router (port 3002)..."
if ss -tlnp | grep -q ":3002 "; then
    ok "BGE-M3 router already running — skipping"
else
    BGE_SCRIPT=$(find "$HOME/AITC" -name "bge_router.py" 2>/dev/null | head -1)
    if [ -n "$BGE_SCRIPT" ]; then
        cd "$(dirname $BGE_SCRIPT)"
        nohup $VLLM_PYTHON bge_router.py > "$LOG_DIR/bge_router.log" 2>&1 &
        echo $! > "$LOG_DIR/bge_router.pid"
        cd "$PROJECT_DIR"
        wait_for_port 3002 "BGE-M3 router" 30
    else
        warn "bge_router.py not found — skipping"
    fi
fi
echo ""

# ── vLLM Models: launch async, no blocking wait ───────────────
# These models take 2-5 minutes to load. They launch in background.
# The fallback chain handles requests until they are ready.
# Monitor readiness: tail -f ~/AITC/logs/aitc-model-ready.log

log "vLLM Models — launching in background (non-blocking)..."
echo ""

# Launch 14B first — gets IPC socket before 3B initializes
launch_vllm 8000 "Qwen2.5-14B" \
    "$QWEN_SNAPSHOT" 0.25 8192 \
    "qwen.log" "qwen.pid"
sleep 10
# Now launch 3B
launch_vllm 8001 "Qwen2.5-3B" \
    "$QWEN3B_SNAPSHOT" 0.05 4096 \
    "qwen3b.log" "qwen3b.pid" "--enforce-eager"


echo ""
warn "vLLM models are loading in background — this takes 2-5 minutes"
warn "FastAPI fallback chain is active — system is already serving requests"
warn "Monitor model readiness: tail -f $LOG_DIR/aitc-model-ready.log"

# Launch cache warmup in background after models are ready
# Prevents cold start for first real user after every restart
nohup bash "$AITC_DIR/warmup.sh" >> "$LOG_DIR/warmup.log" 2>&1 &
echo "[$(date '+%H:%M:%S')] Cache warmup launched (PID=$!) — pre-populating response cache" >> "$READY_LOG"
echo ""

# ── Background readiness monitor ─────────────────────────────
# Watches vLLM ports and logs when each model becomes ready
# Runs as a background job — does not block startup
(
    READY_LOG="$LOG_DIR/aitc-model-ready.log"
    echo "[$(date '+%H:%M:%S')] Model readiness monitor started" > "$READY_LOG"

    qwen3b_ready=false
    qwen14b_ready=false

    for i in $(seq 1 300); do
        sleep 2

        if [ "$qwen3b_ready" = false ] && ss -tlnp | grep -q ":8001 "; then
            # Verify model actually responds
            if curl -s --max-time 3 http://localhost:8001/v1/models | grep -q "id"; then
                qwen3b_ready=true
                echo "[$(date '+%H:%M:%S')] ✅ Qwen 2.5-3B ready on port 8001 (${i}0s after launch)" >> "$READY_LOG"
                # Warmup
                curl -s -X POST http://localhost:8001/v1/chat/completions \
                    -H "Content-Type: application/json" \
                    -d "{\"model\":\"$QWEN3B_SNAPSHOT\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":5}" \
                    > /dev/null 2>&1
                echo "[$(date '+%H:%M:%S')] ✅ Qwen 2.5-3B warmup complete" >> "$READY_LOG"
            fi
        fi

        if [ "$qwen14b_ready" = false ] && ss -tlnp | grep -q ":8000 "; then
            if curl -s --max-time 3 http://localhost:8000/v1/models | grep -q "id"; then
                qwen14b_ready=true
                echo "[$(date '+%H:%M:%S')] ✅ Qwen 2.5-14B ready on port 8000 (${i}0s after launch)" >> "$READY_LOG"
                # Warmup
                curl -s -X POST http://localhost:8000/v1/chat/completions \
                    -H "Content-Type: application/json" \
                    -d "{\"model\":\"$QWEN_SNAPSHOT\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}],\"max_tokens\":5}" \
                    > /dev/null 2>&1
                echo "[$(date '+%H:%M:%S')] ✅ Qwen 2.5-14B warmup complete" >> "$READY_LOG"
            fi
        fi

        if [ "$qwen3b_ready" = true ] && [ "$qwen14b_ready" = true ]; then
            echo "[$(date '+%H:%M:%S')] ✅ All models ready — system fully operational" >> "$READY_LOG"
            break
        fi
    done
) &

# ── Immediate status — only lightweight services ──────────────
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Service Status — Lightweight Services${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

check_svc() {
    local port=$1 label=$2
    if ss -tlnp | grep -q ":$port "; then
        echo -e "  ${GREEN}✅${NC} $label → http://192.168.20.169:$port"
    else
        echo -e "  ${RED}❌${NC} $label → port $port not responding"
    fi
}

check_svc 3000 "Next.js Frontend    "
check_svc 3001 "FastAPI Backend     "
check_svc 3002 "BGE-M3 Router      "
check_svc $XBRL_PORT "MCP Server (XBRL)  "
echo ""
echo -e "  ${YELLOW}⏳${NC} Qwen 2.5-3B  → loading in background (port 8001)"
echo -e "  ${YELLOW}⏳${NC} Qwen 2.5-14B → loading in background (port 8000)"
echo ""
echo -e "  Monitor: ${BLUE}tail -f $LOG_DIR/aitc-model-ready.log${NC}"
echo -e "  Logs:    ${BLUE}$LOG_DIR/${NC}"
echo ""

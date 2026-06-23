import os
import sys
import asyncio
import time
import sqlite3
import uvicorn

from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from src.mappings.company_stock_code_array import CompanyStockCodeArray
from fastapi.middleware.cors import CORSMiddleware
from src.types.langgraph_state_types import OverallState
from src.agent.graph import graph
from src.api.chatbot import chatbot_router
from langchain_core.messages import HumanMessage, AIMessage

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── App startup time (for uptime reporting) ───────────────────
_START_TIME = time.time()

app = FastAPI(
    title="AITC Credit Investigation Chatbot",
    description="On-premise Taiwan FSC XBRL credit investigation API powered by Breeze2-8B",
    version="1.0.0",
)

api_router = APIRouter()
api_router.include_router(chatbot_router)
app.include_router(api_router)

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://192.168.20.169:3001",
    "http://192.168.20.169:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health check endpoint ─────────────────────────────────────
# Why this exists:
# Without it, the only way to know if the backend is alive is to
# send a real query and wait 2-3 minutes for Breeze2 to respond.
# A health check responds in milliseconds — used by start.sh,
# monitoring tools, and teammates to verify the system before use.
#
# Checks three things:
# 1. API server itself is responding
# 2. XBRL database is reachable and has data
# 3. vLLM model server is reachable (HTTP ping only, no inference)

@app.get("/health", tags=["System"])
async def health_check():
    """
    System health check — returns status of all components.
    Use this to verify the system is ready before sending queries.
    Expected response time: < 500ms
    """
    status = {
        "status": "ok",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "components": {}
    }
    all_ok = True

    # ── Check 1: XBRL database ────────────────────────────────
    db_path = os.environ.get("XBRL_DB_PATH", "FinancialStatementXBRL.db")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT COUNT(*) FROM financial_metric_value"
        ).fetchone()
        conn.close()
        status["components"]["xbrl_database"] = {
            "status": "ok",
            "path": db_path,
            "financial_metric_rows": row[0],
        }
    except Exception as e:
        status["components"]["xbrl_database"] = {
            "status": "error",
            "error": str(e),
        }
        all_ok = False

    # ── Check 2: vLLM model server ────────────────────────────
    vllm_url = os.environ.get("VLLM_BASE_URL", "http://localhost:8080/v1")
    try:
        import urllib.request
        req = urllib.request.urlopen(
            f"{vllm_url}/models", timeout=3
        )
        status["components"]["vllm_server"] = {
            "status": "ok",
            "url": vllm_url,
            "http_status": req.status,
        }
    except Exception as e:
        status["components"]["vllm_server"] = {
            "status": "unreachable",
            "url": vllm_url,
            "note": "vLLM may still be loading — retry in 30s",
        }
        # vLLM unreachable is a warning, not a fatal error
        # (it may still be starting up)

    # ── Check 3: MCP server ───────────────────────────────────
    mcp_port = os.environ.get("XBRL_PORT", "8091")
    mcp_url = f"http://localhost:{mcp_port}/sse"
    try:
        import urllib.request
        req = urllib.request.urlopen(mcp_url, timeout=2)
        status["components"]["mcp_server"] = {
            "status": "ok",
            "url": mcp_url,
        }
    except Exception as e:
        status["components"]["mcp_server"] = {
            "status": "unreachable",
            "url": mcp_url,
            "note": "MCP server not running — Claude Code plugin will not work",
        }

    # ── Overall status ─────────────────────────────────────────
    if not all_ok:
        status["status"] = "degraded"
        return JSONResponse(status_code=503, content=status)

    return JSONResponse(status_code=200, content=status)


@app.get("/", tags=["System"])
async def root():
    """API root — basic info and available endpoints."""
    return {
        "name": "AITC Credit Investigation Chatbot",
        "version": "1.0.0",
        "endpoints": {
            "health":  "GET /health",
            "chatbot": "GET /chatbot/{user_input}",
            "docs":    "GET /docs",
        },
        "model": os.environ.get("VLLM_MODEL", "unknown").split("/")[-1],
        "uptime_seconds": round(time.time() - _START_TIME, 1),
    }


async def terminal_chat():
    while True:
        user_input = input("You: ")
        if user_input.lower() in ("exit", "quit"):
            break
        try:
            graph_answer = graph.invoke(
                {
                    "messages": [HumanMessage(content=user_input)],
                    "user_input": user_input,
                },
                config={"configurable": {"thread_id": "1"}},
            )
        except Exception as err:
            print("Error:", err, file=sys.stderr)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3001)
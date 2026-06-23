import time
import os
import asyncio

from fastapi import APIRouter, Query, HTTPException
from dotenv import load_dotenv

load_dotenv()

from src.agent.graph import graph
from src.types.langgraph_state_types import OverallState
from src.services.query_cache import get_cached_answer, save_cached_answer

chatbot_router = APIRouter()

# ── Model / provider display ──────────────────────────────────
MODEL_NAME = os.getenv("VLLM_MODEL", os.getenv("OLLAMA_MODEL", "unknown")).split("/")[-1]
PROVIDER   = "vLLM Breeze2 (On-Premise)" if os.getenv("VLLM_MODEL") else "Ollama (On-Premise)"

# ── Timeout ───────────────────────────────────────────────────
# Complex XBRL queries can take 2–3 minutes on first run.
# 180 seconds gives enough headroom while preventing indefinite hangs.
PIPELINE_TIMEOUT_SECONDS = int(os.getenv("PIPELINE_TIMEOUT", "180"))


@chatbot_router.get("/chatbot/{user_input}")
async def get_chatbot_answer(
    user_input: str,
    bypass_cache: bool = Query(default=False, description="Skip cache and run full pipeline"),
):
    start = time.perf_counter()

    # ── Cache check ───────────────────────────────────────────
    if not bypass_cache:
        cached = get_cached_answer(user_input)
        if cached:
            elapsed = round(time.perf_counter() - start, 4)
            print("=" * 60)
            print(f"✅ CACHE HIT")
            print(f"🤖 Model    : {MODEL_NAME} ({PROVIDER})")
            print(f"❓ Question : {user_input[:60]}")
            print(f"⏱️  Runtime  : {elapsed}s (from cache)")
            print("=" * 60)
            return {
                "answer": cached,
                "model": MODEL_NAME,
                "provider": PROVIDER,
                "response_time_seconds": elapsed,
                "cached": True,
                "dispatch_decision": "private",
            }
    else:
        print(f"[chatbot] bypass_cache=true — running full pipeline")

    # ── Full pipeline with timeout ────────────────────────────
    try:
        graph_answer = await asyncio.wait_for(
            asyncio.to_thread(graph.invoke, {"user_input": user_input}),
            timeout=PIPELINE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        elapsed = round(time.perf_counter() - start, 2)
        timeout_msg = (
            f"查詢逾時（超過 {PIPELINE_TIMEOUT_SECONDS} 秒）。"
            "請嘗試簡化問題，或稍後再試。"
        )
        print("=" * 60)
        print(f"⏱️  TIMEOUT after {elapsed}s — {user_input[:60]}")
        print("=" * 60)
        return {
            "answer": timeout_msg,
            "model": MODEL_NAME,
            "provider": PROVIDER,
            "response_time_seconds": elapsed,
            "cached": False,
            "dispatch_decision": "timeout",
            "error": "pipeline_timeout",
        }

    elapsed = round(time.perf_counter() - start, 2)
    answer  = graph_answer.get("answer", "")

    # ── Terminal benchmark log ────────────────────────────────
    print("=" * 60)
    print(f"🤖 Model    : {MODEL_NAME} ({PROVIDER})")
    print(f"❓ Question : {user_input[:60]}")
    print(f"⏱️  Runtime  : {elapsed}s")
    print(f"📦 Cached   : No (fresh run)")
    print(f"📝 Answer   :")
    print("-" * 60)
    for line in answer.splitlines():
        print(f"   {line}")
    if not answer.strip():
        print("   (empty answer)")
    print("=" * 60)

    # ── Save to cache ─────────────────────────────────────────
    if not bypass_cache:
        save_cached_answer(user_input, answer)

    return {
        "answer": answer,
        "model": MODEL_NAME,
        "provider": PROVIDER,
        "response_time_seconds": elapsed,
        "cached": False,
        "dispatch_decision": graph_answer.get("dispatch_decision", "private"),
    }

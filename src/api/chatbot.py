import time
import os

from fastapi import APIRouter, Query
from dotenv import load_dotenv

load_dotenv()

from src.agent.graph import graph
from src.types.langgraph_state_types import OverallState
from src.services.query_cache import get_cached_answer, save_cached_answer

chatbot_router = APIRouter()

# ── Show correct model name regardless of provider ────────────
MODEL_NAME = os.getenv("VLLM_MODEL", os.getenv("OLLAMA_MODEL", "unknown")).split("/")[-1]
PROVIDER = "vLLM Breeze2 (On-Premise)" if os.getenv("VLLM_MODEL") else "Ollama (On-Premise)"


@chatbot_router.get("/chatbot/{user_input}")
async def get_chatbot_answer(
    user_input: str,
    bypass_cache: bool = Query(default=False, description="Skip cache and run full pipeline"),
):
    start = time.perf_counter()

    # Cache check — skipped if bypass_cache=true
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

    # Full pipeline
    graph_answer = graph.invoke({"user_input": user_input})

    elapsed = round(time.perf_counter() - start, 2)
    answer = graph_answer.get("answer", "")

    # ── Print full benchmark result in terminal ────────────────
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

    # Save to cache only if not bypassing
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
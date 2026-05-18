import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Which embedding provider to use ───────────────────────────
# Set in .env:
#   EMBEDDING_PROVIDER=ollama   → uses Mac Mini nomic-embed-text
#   EMBEDDING_PROVIDER=openai   → uses text-embedding-3-small (default)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai").lower()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.1.102:11434")
OLLAMA_EMBED_MODEL = "nomic-embed-text"

OPENAI_EMBED_MODEL = "text-embedding-3-small"

# Each provider saves to its own cache file so you can compare both
CACHE_PATHS = {
    "ollama": Path("src/services/xbrl_embeddings_cache_ollama.json"),
    "openai": Path("src/services/xbrl_embeddings_cache_openai.json"),
}
# Fallback to original path for backward compatibility
LEGACY_CACHE_PATH = Path("src/services/xbrl_embeddings_cache.json")

HIGH_CONFIDENCE_THRESHOLD = 85.0
AMBIGUITY_MARGIN = 5.0

# ── In-memory cache with invalidation support ──────────────────
# Using a dict instead of @lru_cache so we can invalidate when
# the cache file is built during the same process lifetime.
_cache_store: Dict = {}
_cache_loaded: bool = False


def invalidate_cache() -> None:
    """
    Force reload of embeddings cache on next call.
    Call this after running build_xbrl_embeddings.py if the app
    is already running — prevents stale empty cache.
    """
    global _cache_store, _cache_loaded
    _cache_store = {}
    _cache_loaded = False
    logger.info("[vector_candidate_search] cache invalidated — will reload on next call")


# ── Embedding functions ────────────────────────────────────────

def get_embedding_ollama(text: str) -> List[float]:
    import httpx
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def get_embedding_openai(text: str) -> List[float]:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model=OPENAI_EMBED_MODEL,
        input=text,
    )
    return response.data[0].embedding


def get_embedding(text: str) -> List[float]:
    """
    Get embedding using the configured provider.
    Switch by setting EMBEDDING_PROVIDER=ollama or EMBEDDING_PROVIDER=openai in .env
    """
    if EMBEDDING_PROVIDER == "openai":
        return get_embedding_openai(text)
    else:
        return get_embedding_ollama(text)


# ── Cosine similarity ──────────────────────────────────────────

def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0.0:
        return 0.0
    return float(np.dot(a, b) / norm)


# ── Load embeddings cache ──────────────────────────────────────

def _load_embeddings_cache() -> Dict:
    """
    Load the cache for the active provider.
    Uses in-memory store with manual invalidation support.
    Falls back to legacy path for backward compatibility.

    FIX vs previous version: @lru_cache would permanently return {}
    if cache file didn't exist at startup. This version reloads
    automatically on next call after invalidate_cache().
    """
    global _cache_store, _cache_loaded

    if _cache_loaded:
        return _cache_store

    # Try provider-specific cache first
    provider_path = CACHE_PATHS.get(EMBEDDING_PROVIDER)
    if provider_path and provider_path.exists():
        try:
            data = json.loads(provider_path.read_text(encoding="utf-8"))
            _cache_store = data
            _cache_loaded = True
            logger.info(
                "[vector_candidate_search] loaded %d embeddings from %s (provider=%s)",
                len(data), provider_path, EMBEDDING_PROVIDER,
            )
            return _cache_store
        except Exception as exc:
            logger.error(
                "[vector_candidate_search] failed to load %s: %s",
                provider_path, exc,
            )

    # Try legacy path
    if LEGACY_CACHE_PATH.exists():
        try:
            data = json.loads(LEGACY_CACHE_PATH.read_text(encoding="utf-8"))
            _cache_store = data
            _cache_loaded = True
            logger.info(
                "[vector_candidate_search] loaded %d embeddings from legacy cache %s",
                len(data), LEGACY_CACHE_PATH,
            )
            return _cache_store
        except Exception as exc:
            logger.error(
                "[vector_candidate_search] failed to load legacy cache: %s", exc,
            )

    # Cache not found — log clearly and return empty
    # Do NOT set _cache_loaded = True so next call will retry
    logger.warning(
        "[vector_candidate_search] cache not found for provider '%s' — "
        "run scripts/build_xbrl_embeddings.py first. Falling back to keyword matching.",
        EMBEDDING_PROVIDER,
    )
    return {}


# ── Main search function ───────────────────────────────────────

def vector_find_candidates(
    field_name: str,
    statement_type: str,
    limit: int = 8,
    company_code: Optional[str] = None,
) -> List[Dict]:
    """
    Find XBRL concept candidates by semantic vector similarity.

    Chinese fields benefit most — the embedding model understands that
    現金及約當現金 and CashAndCashEquivalents mean the same thing,
    so Chinese queries correctly rank the right XBRL concept first.

    Returns empty list if cache not built — caller should fall back
    to keyword matching (find_candidates).
    """
    cache = _load_embeddings_cache()
    if not cache:
        logger.warning(
            "[vector_candidate_search] cache is empty — returning no candidates for '%s'. "
            "Caller should fall back to keyword matching.",
            field_name,
        )
        return []

    try:
        query_embedding = get_embedding(field_name)
    except Exception as exc:
        logger.error(
            "[vector_candidate_search] embedding failed for '%s': %s — returning []",
            field_name, exc,
        )
        return []

    scored = []
    for concept_id, entry in cache.items():
        entry_statement = entry.get("statement_type")

        # Filter by statement type when both are known
        if statement_type and entry_statement and entry_statement != statement_type:
            continue

        sim = cosine_similarity(query_embedding, entry["embedding"])
        score = round(sim * 100, 3)
        if score <= 0:
            continue

        scored.append({
            "concept_name": concept_id,
            "zh_tw": entry.get("zh_tw"),
            "en": entry.get("en"),
            "code": entry.get("code"),
            "statement_type": entry_statement or statement_type,
            "score": score,
            "mapped_from": field_name,
            "mapping_queries": [],
            "mapping_aliases": [],
        })

    scored.sort(key=lambda x: -x["score"])
    results = scored[:limit]

    if results:
        top = results[0]["score"]
        second = results[1]["score"] if len(results) > 1 else 0.0
        margin = top - second
        for item in results:
            item["is_high_confidence"] = item["score"] >= HIGH_CONFIDENCE_THRESHOLD
            item["is_ambiguous"] = margin < AMBIGUITY_MARGIN

    logger.info(
        "[vector_candidate_search] provider=%s field='%s' statement='%s' "
        "found %d candidates top_score=%.1f is_high_confidence=%s",
        EMBEDDING_PROVIDER,
        field_name,
        statement_type,
        len(results),
        results[0]["score"] if results else 0.0,
        results[0].get("is_high_confidence", False) if results else False,
    )
    return results
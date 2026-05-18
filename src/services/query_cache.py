# ─────────────────────────────────────────────────────────────
# src/services/query_cache.py
# NEW FILE — response cache for final answers
#
# WHY:
#   Financial data for a given company and reporting period does not
#   change between queries. Asking the same question twice should
#   return instantly — not run the full 6–17 call pipeline again.
#
# HOW:
#   Keyed by MD5 hash of the normalized question text.
#   Stored in a JSON file on disk.
#   Zero LLM calls on cache hit.
# ─────────────────────────────────────────────────────────────

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_PATH = Path("src/services/query_result_cache.json")


def _normalize(text: str) -> str:
    """
    Normalize before hashing so minor differences (spaces, case)
    map to the same cache key.
    """
    return text.strip().lower()


def make_cache_key(user_input: str) -> str:
    return hashlib.md5(_normalize(user_input).encode("utf-8")).hexdigest()


def get_cached_answer(user_input: str) -> Optional[str]:
    """
    Return cached answer if it exists, else None.
    """
    if not CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        answer = cache.get(make_cache_key(user_input))
        if answer:
            logger.info("[query_cache] HIT for: %s", user_input[:60])
        return answer
    except Exception as exc:
        logger.warning("[query_cache] read failed: %s", exc)
        return None


def save_cached_answer(user_input: str, answer: str) -> None:
    """
    Save answer to cache after a successful pipeline run.
    """
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    try:
        cache[make_cache_key(user_input)] = answer
        CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[query_cache] SAVED for: %s", user_input[:60])
    except Exception as exc:
        logger.warning("[query_cache] write failed: %s", exc)


def clear_cache() -> None:
    """
    Delete all cached answers.
    Call this when the underlying XBRL data is updated.
    """
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        logger.info("[query_cache] cache cleared")
"""
Query result cache with TTL.
Stores answers in query_result_cache.json keyed by MD5 hash of the question.
Entries expire after CACHE_TTL_HOURS (default 24 hours).

Why TTL matters:
  The XBRL database is updated periodically. Without TTL, a query answered
  today returns the same answer in 6 months even if new data has been imported.
  24-hour TTL balances performance (most questions repeat within a session)
  with freshness (stale answers expire overnight).
"""

import os
import json
import hashlib
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────
CACHE_FILE      = Path(os.getenv("CACHE_FILE", "src/services/query_result_cache.json"))
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))
CACHE_TTL_SECS  = CACHE_TTL_HOURS * 3600

# ── In-memory cache (loaded once at startup) ──────────────────
_cache: dict = {}
_loaded: bool = False


def _load() -> None:
    """Load cache from disk into memory. Called once on first access."""
    global _cache, _loaded
    if _loaded:
        return
    if CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            # Support both old format (str values) and new format (dict with ts)
            if isinstance(raw, dict):
                _cache = raw
        except Exception as e:
            logger.warning(f"[cache] Failed to load cache file: {e} — starting fresh")
            _cache = {}
    _loaded = True


def _save() -> None:
    """Persist cache to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(_cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[cache] Failed to save cache: {e}")


def _key(question: str) -> str:
    """MD5 hash of the question used as cache key."""
    return hashlib.md5(question.strip().lower().encode()).hexdigest()


def _is_expired(entry: dict | str) -> bool:
    """Return True if the cache entry is expired or in old format without timestamp."""
    if isinstance(entry, str):
        # Old format — no timestamp, treat as expired so it gets refreshed
        return True
    ts = entry.get("cached_at", 0)
    return (time.time() - ts) > CACHE_TTL_SECS


def get_cached_answer(question: str) -> str | None:
    """
    Return cached answer if it exists and is not expired.
    Returns None if cache miss or entry is stale.
    """
    _load()
    key   = _key(question)
    entry = _cache.get(key)
    if entry is None:
        return None
    if _is_expired(entry):
        logger.info(f"[cache] EXPIRED for: {question[:50]}")
        del _cache[key]
        return None
    answer = entry.get("answer") if isinstance(entry, dict) else entry
    logger.info(f"[cache] HIT for: {question[:50]}")
    return answer


def save_cached_answer(question: str, answer: str) -> None:
    """
    Save answer to cache with current timestamp.
    Automatically purges all expired entries before saving.
    """
    _load()
    _purge_expired()
    key = _key(question)
    _cache[key] = {
        "question":  question,
        "answer":    answer,
        "cached_at": time.time(),
        "expires_at": time.time() + CACHE_TTL_SECS,
    }
    _save()
    logger.info(f"[cache] SAVED for: {question[:50]} (TTL: {CACHE_TTL_HOURS}h)")


def _purge_expired() -> None:
    """Remove all expired entries from the in-memory cache."""
    expired_keys = [k for k, v in _cache.items() if _is_expired(v)]
    for k in expired_keys:
        del _cache[k]
    if expired_keys:
        logger.info(f"[cache] Purged {len(expired_keys)} expired entries")


def clear_cache() -> int:
    """
    Clear all cache entries. Call this after importing new XBRL data
    to ensure fresh answers are generated.
    Returns number of entries cleared.
    """
    global _cache
    _load()
    count  = len(_cache)
    _cache = {}
    _save()
    logger.info(f"[cache] Cleared {count} entries")
    return count


def cache_stats() -> dict:
    """Return cache statistics for monitoring."""
    _load()
    now    = time.time()
    valid  = sum(1 for v in _cache.values() if not _is_expired(v))
    expired= len(_cache) - valid
    return {
        "total_entries":   len(_cache),
        "valid_entries":   valid,
        "expired_entries": expired,
        "ttl_hours":       CACHE_TTL_HOURS,
        "cache_file":      str(CACHE_FILE),
    }

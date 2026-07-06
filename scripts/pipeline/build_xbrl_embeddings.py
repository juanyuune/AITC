import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.services.vector_candidate_search import get_embedding, EMBEDDING_PROVIDER, invalidate_cache
from src.services.account_title_matcher import load_dictionary

# Save to provider-specific file so both caches can coexist
OUTPUT_PATHS = {
    "ollama": Path("src/services/xbrl_embeddings_cache_ollama.json"),
    "openai": Path("src/services/xbrl_embeddings_cache_openai.json"),
}
OUTPUT_PATH = OUTPUT_PATHS.get(EMBEDDING_PROVIDER, Path("src/services/xbrl_embeddings_cache.json"))
LOG_EVERY = 50


def build_concept_text(item: dict) -> str:
    """
    Combine all available labels into one rich text string.
    Chinese labels come first — this system is Chinese-primary.
    The more labels included, the better the embedding captures meaning.

    Example: "現金及約當現金 | Cash and cash equivalents | 現金 | Cash"
    """
    parts = []
    # Chinese first — system is Chinese-primary (Taiwan IFRS)
    for key in ("zh_tw", "mapping_canonical_zh", "en", "mapping_canonical_en"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    for alias in item.get("mapping_aliases", []):
        if isinstance(alias, str) and alias.strip():
            parts.append(alias.strip())

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return " | ".join(unique)


def main():
    model_name = (
        "text-embedding-3-small (OpenAI)"
        if EMBEDDING_PROVIDER == "openai"
        else f"nomic-embed-text (Ollama @ {os.getenv('OLLAMA_BASE_URL', 'http://192.168.1.102:11434')})"
    )

    print("=" * 60)
    print("XBRL Embedding Cache Builder")
    print(f"Provider:   {EMBEDDING_PROVIDER.upper()}")
    print(f"Model:      {model_name}")
    print(f"Output:     {OUTPUT_PATH}")
    print("=" * 60)

    print("\nLoading XBRL dictionary...")
    dictionary = load_dictionary()
    total = len(dictionary)
    print(f"Found {total} concepts. Building embeddings...\n")

    cache = {}
    failed = []
    skipped = 0
    start = time.time()

    for i, item in enumerate(dictionary):
        concept_id = item.get("concept_name")
        if not concept_id:
            skipped += 1
            continue

        text = build_concept_text(item)
        if not text:
            skipped += 1
            continue

        try:
            embedding = get_embedding(text)
            cache[concept_id] = {
                "embedding": embedding,
                "zh_tw": item.get("zh_tw"),
                "en": item.get("en"),
                "code": item.get("code"),
                "statement_type": item.get("statement_type"),
                "source_text": text,
                "provider": EMBEDDING_PROVIDER,
            }
        except Exception as exc:
            failed.append(concept_id)
            print(f"  WARN: failed to embed {concept_id}: {exc}")
            continue

        if (i + 1) % LOG_EVERY == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 1
            remaining = (total - i - 1) / rate if rate > 0 else 0
            success_rate = len(cache) / (i + 1 - skipped) * 100 if (i + 1 - skipped) > 0 else 0
            print(
                f"  {i + 1}/{total} concepts  "
                f"({elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining, "
                f"{success_rate:.0f}% success)"
            )

    # ── Save cache ─────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(cache, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── NEW: invalidate in-memory cache so running app picks up ──
    # If app.py is already running, this tells vector_candidate_search
    # to reload from disk on the next request — no restart needed.
    try:
        invalidate_cache()
        print("\n✅ In-memory cache invalidated — running app will reload automatically.")
    except Exception as exc:
        print(f"\n  NOTE: Could not invalidate in-memory cache: {exc}")
        print("  Restart app.py to pick up the new embeddings.")

    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"Done in {elapsed:.0f}s")
    print(f"Provider:  {EMBEDDING_PROVIDER.upper()}")
    print(f"Embedded:  {len(cache)} concepts")
    if skipped:
        print(f"Skipped:   {skipped} (no concept_id or no text)")
    if failed:
        print(f"Failed:    {len(failed)} — {failed[:5]}{'...' if len(failed) > 5 else ''}")
    print(f"Saved to:  {OUTPUT_PATH}")
    print("=" * 60)
    print(f"\nTo switch provider, change EMBEDDING_PROVIDER in .env and restart app.py")


if __name__ == "__main__":
    main()
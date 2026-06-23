# src/services/inference_dispatcher.py
# Reads retrieved_sources from LangGraph state and returns
# the dispatch decision: "private" or "cloud".
#
# Decision rules (first match wins):
#   1. Any source in PRIVATE_SOURCES  → private
#   2. Any source not in PUBLIC_SOURCES → private (unknown = safe default)
#   3. All sources in PUBLIC_SOURCES   → cloud

from src.services.data_classifier_config import PRIVATE_SOURCES, PUBLIC_SOURCES


def dispatch(retrieved_sources: list[str]) -> str:
    """
    Returns 'private' or 'cloud' based on source classification.
    Defaults to 'private' when sources are empty or unknown.
    """
    if not retrieved_sources:
        return "private"

    for source in retrieved_sources:
        if source in PRIVATE_SOURCES:
            print(f"[dispatcher] source '{source}' is PRIVATE → on-premise")
            return "private"
        if source not in PUBLIC_SOURCES:
            print(f"[dispatcher] source '{source}' unknown → defaulting to on-premise")
            return "private"

    print(f"[dispatcher] all sources confirmed PUBLIC → cloud")
    return "cloud"

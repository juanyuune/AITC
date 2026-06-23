# src/services/data_sanitizer.py
# Strips private field names from evidence before cloud dispatch.
# Raises ValueError if nothing remains — dispatcher catches this
# and forces on-premise fallback.

from src.services.data_classifier_config import PRIVATE_FIELDS


def sanitize_for_cloud(evidence: dict) -> dict:
    """
    Remove all private fields from evidence dict.
    Raises ValueError if evidence is entirely private.
    """
    sanitized = {
        k: v for k, v in evidence.items()
        if k not in PRIVATE_FIELDS
    }
    if not sanitized:
        raise ValueError(
            "All evidence fields are private — falling back to on-premise."
        )
    return sanitized


def sanitize_evidence_list(evidence_list: list[dict]) -> list[dict]:
    """
    Sanitize a list of evidence items.
    Items that become empty after sanitization are dropped.
    Raises ValueError if all items are dropped.
    """
    sanitized = []
    for item in evidence_list:
        try:
            sanitized.append(sanitize_for_cloud(item))
        except ValueError:
            continue

    if not sanitized:
        raise ValueError(
            "All evidence is private after sanitization — falling back to on-premise."
        )
    return sanitized

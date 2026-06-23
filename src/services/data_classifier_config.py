# src/services/data_classifier_config.py
# Single source of truth for data source classification.
# All dispatcher and sanitizer files import from here.
# To reclassify a source, edit only this file.

# ── Private sources ───────────────────────────────────────────
# These never leave the office network.
PRIVATE_SOURCES = {
    "FinancialStatementXBRL.db",
    "document_knowledge_base.json",
    "session_document_store",
    "uploads/documents",
}

# ── Public sources ────────────────────────────────────────────
# Only these are eligible for cloud dispatch after sanitization.
PUBLIC_SOURCES = {
    "published_xbrl_reports",
    "web_search",
    "public_benchmarks",
}

# ── Private fields ────────────────────────────────────────────
# These field names are stripped before any cloud dispatch.
PRIVATE_FIELDS = [
    "client_id",
    "account_number",
    "loan_amount",
    "credit_score",
    "internal_rating",
    "collateral",
    "bank_officer_notes",
    "personal_income",
]

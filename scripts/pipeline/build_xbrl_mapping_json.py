#!/usr/bin/env python3
import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional


LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
PERIOD_END_ROLE = "http://www.xbrl.org/2003/role/periodEndLabel"
PERIOD_START_ROLE = "http://www.xbrl.org/2003/role/periodStartLabel"
TOTAL_ROLE = "http://www.xbrl.org/2003/role/totalLabel"
TERSE_ROLE = "http://www.xbrl.org/2003/role/terseLabel"
NEGATED_PERIOD_END_ROLE = "http://www.xbrl.org/2009/role/negatedPeriodEndLabel"

ZH_LABEL_PRIORITY = (
    LABEL_ROLE,
    PERIOD_END_ROLE,
    PERIOD_START_ROLE,
    TOTAL_ROLE,
)
EN_LABEL_PRIORITY = (
    LABEL_ROLE,
    PERIOD_END_ROLE,
    PERIOD_START_ROLE,
    TOTAL_ROLE,
)
ALIAS_LABEL_ROLES = (
    LABEL_ROLE,
    PERIOD_END_ROLE,
    PERIOD_START_ROLE,
    TOTAL_ROLE,
    TERSE_ROLE,
)
KNOWN_INDUSTRY_TYPES = ("BASI", "BD", "CI", "FH", "INS", "MIM")


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value).lower().strip())


def unique_non_empty(values: Iterable[Optional[str]]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def label_for_roles(labels: Dict, roles: Iterable[str], lang: str) -> Optional[str]:
    for role in roles:
        value = labels.get(role, {}).get(lang)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def simplified_zh_alias(value: str) -> List[str]:
    aliases = []
    replacements = (
        ("資產負債表帳列之", ""),
        ("合計", ""),
        ("餘額", ""),
    )
    current = value
    for old, new in replacements:
        if old in current:
            current = current.replace(old, new).strip()
            aliases.append(current)
    return aliases


def statement_type_from_roles(roles: List[str]) -> List[str]:
    statement_types = []
    for role in roles:
        if "BalanceSheet" in role and "balance_sheet" not in statement_types:
            statement_types.append("balance_sheet")
        if (
            ("StatementOfComprehensiveIncome" in role or "ProfitLoss" in role)
            and "comprehensive_income_statement" not in statement_types
        ):
            statement_types.append("comprehensive_income_statement")
        if "StatementOfCashFlows" in role and "statement_of_cash_flows" not in statement_types:
            statement_types.append("statement_of_cash_flows")
    return statement_types


def industry_types_from_item(item: Dict) -> List[str]:
    texts: List[str] = []
    for key in ("source_files", "source_hrefs", "families", "concept_name", "search_text"):
        value = item.get(key)
        if isinstance(value, list):
            texts.extend(str(entry) for entry in value if entry)
        elif value:
            texts.append(str(value))
    for presentation in item.get("presentation") or []:
        if not isinstance(presentation, dict):
            continue
        for key in ("source_file", "parent_concept"):
            value = presentation.get(key)
            if value:
                texts.append(str(value))
        path = presentation.get("path")
        if isinstance(path, list):
            texts.extend(str(entry) for entry in path if entry)
        elif path:
            texts.append(str(path))

    matched = []
    lower_texts = [text.lower() for text in texts]
    for industry_type in KNOWN_INDUSTRY_TYPES:
        token = industry_type.lower()
        patterns = (
            f"-{token}-",
            f"_{token}_",
            f"_{token}-",
            f"-{token}_",
            f"/{token}/",
            f"bsci-{token}",
            f"notes-{token}",
            f"scf-{token}",
        )
        if any(pattern in text for pattern in patterns for text in lower_texts):
            matched.append(industry_type)
    return matched


def label_role_summary(labels: Dict) -> Dict[str, Dict[str, str]]:
    compact = {}
    for role, lang_map in sorted(labels.items()):
        if not isinstance(lang_map, dict):
            continue
        compact[role] = {
            lang: text
            for lang, text in sorted(lang_map.items())
            if isinstance(text, str) and text.strip()
        }
    return compact


def build_mapping_record(item: Dict) -> Dict:
    labels = item.get("labels") or {}
    zh = label_for_roles(labels, ZH_LABEL_PRIORITY, "zh-tw")
    en = label_for_roles(labels, EN_LABEL_PRIORITY, "en")
    code = item.get("code") or labels.get(TERSE_ROLE, {}).get("en")

    alias_candidates: List[Optional[str]] = [
        zh,
        en,
        item.get("zh_tw"),
        item.get("en"),
        item.get("name"),
        item.get("concept_name"),
    ]
    for role in ALIAS_LABEL_ROLES:
        role_labels = labels.get(role, {})
        alias_candidates.append(role_labels.get("zh-tw"))
        alias_candidates.append(role_labels.get("en"))
    if labels.get(NEGATED_PERIOD_END_ROLE, {}).get("en"):
        alias_candidates.append(labels[NEGATED_PERIOD_END_ROLE]["en"])
    if code:
        alias_candidates.append(code)

    zh_aliases = []
    for value in alias_candidates:
        if isinstance(value, str) and re.search(r"[\u4e00-\u9fff]", value):
            zh_aliases.extend(simplified_zh_alias(value))
    aliases = unique_non_empty([*alias_candidates, *zh_aliases])

    return {
        "concept_id": item.get("concept_name"),
        "name": item.get("name"),
        "canonical_zh": zh,
        "canonical_en": en,
        "code": code,
        "statement_types": statement_type_from_roles(item.get("roles") or []),
        "industry_types": industry_types_from_item(item),
        "families": item.get("families") or [],
        "roles": item.get("roles") or [],
        "labels": label_role_summary(labels),
        "aliases": aliases,
        "presentation_paths": [
            {
                "role": entry.get("role"),
                "path": entry.get("path"),
                "parent_concept": entry.get("parent_concept"),
                "source_file": entry.get("source_file"),
            }
            for entry in item.get("presentation") or []
            if isinstance(entry, dict)
        ],
    }


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build field mapping JSON from parsed XBRL dictionary labels.")
    parser.add_argument(
        "--dictionary-json",
        default="src/services/xbrl_data_dictionary_all.json",
        help="Parsed dictionary JSON containing labels and presentation metadata.",
    )
    parser.add_argument(
        "--output",
        default="src/services/xbrl_mapping/concept_mapping.json",
        help="Output mapping JSON path.",
    )
    parser.add_argument(
        "--summary-output",
        default="src/services/xbrl_mapping/summary.json",
        help="Output summary JSON path.",
    )
    parser.add_argument(
        "--taxonomy-root",
        default=None,
        help="Optional taxonomy root path used for traceability in the summary.",
    )
    args = parser.parse_args()

    dictionary_path = Path(args.dictionary_json)
    items = json.loads(dictionary_path.read_text(encoding="utf-8"))
    records = [build_mapping_record(item) for item in items]

    role_counter = Counter()
    for item in items:
        for role in (item.get("labels") or {}).keys():
            role_counter[role] += 1

    summary = {
        "dictionary_json": str(dictionary_path),
        "taxonomy_root": args.taxonomy_root,
        "concept_count": len(records),
        "canonical_zh_count": sum(1 for record in records if record.get("canonical_zh")),
        "canonical_en_count": sum(1 for record in records if record.get("canonical_en")),
        "alias_count": sum(len(record.get("aliases") or []) for record in records),
        "label_role_counts": dict(sorted(role_counter.items())),
    }

    write_json(Path(args.output), records)
    write_json(Path(args.summary_output), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

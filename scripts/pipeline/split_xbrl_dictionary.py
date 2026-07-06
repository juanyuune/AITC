#!/usr/bin/env python3
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List


STATEMENT_FILTERS = {
    "balance_sheet": {
        "role_keywords": ["BalanceSheet"],
        "families": {"BSCI"},
    },
    "comprehensive_income_statement": {
        "role_keywords": ["StatementOfComprehensiveIncome"],
        "families": {"BSCI"},
    },
    "statement_of_cash_flows": {
        "role_keywords": ["StatementOfCashFlows"],
        "families": {"SCF"},
    },
}


def role_matches(item: Dict, statement_type: str) -> bool:
    config = STATEMENT_FILTERS.get(statement_type)
    if not config:
        return bool(item.get("code"))
    has_code = bool(item.get("code"))
    families = set(item.get("families", []))
    roles = item.get("roles", [])
    family_match = bool(families & config["families"])
    role_match = any(
        keyword in role
        for role in roles
        for keyword in config["role_keywords"]
    )
    if statement_type == "statement_of_cash_flows":
        return has_code and (family_match or role_match)
    return has_code and family_match and role_match


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def split_dictionary(items: List[Dict], output_dir: Path) -> Dict[str, Dict]:
    summary: Dict[str, Dict] = {}
    for statement_type in STATEMENT_FILTERS:
        statement_items = [item for item in items if role_matches(item, statement_type)]
        write_json(output_dir / statement_type / "__all__.json", statement_items)

        families_map = defaultdict(list)
        for item in statement_items:
            for family in item.get("families", []):
                families_map[family].append(item)

        family_counts = {}
        for family, family_items in sorted(families_map.items()):
            write_json(output_dir / statement_type / f"{family}.json", family_items)
            family_counts[family] = len(family_items)

        summary[statement_type] = {
            "concept_count": len(statement_items),
            "family_counts": family_counts,
        }

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Split xbrl_data_dictionary_all.json into statement/family subsets.")
    parser.add_argument(
        "--input",
        default="src/services/xbrl_data_dictionary_all.json",
        help="Path to the full XBRL dictionary JSON.",
    )
    parser.add_argument(
        "--output-dir",
        default="src/services/xbrl_dictionary_splits",
        help="Directory to write split dictionaries.",
    )
    parser.add_argument(
        "--summary-output",
        default="src/services/xbrl_dictionary_splits/summary.json",
        help="Summary JSON path.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    summary_output = Path(args.summary_output)

    items = json.loads(input_path.read_text(encoding="utf-8"))
    summary = {
        "input": str(input_path),
        "output_dir": str(output_dir),
        "total_concepts": len(items),
        "statements": split_dictionary(items, output_dir),
    }
    write_json(summary_output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

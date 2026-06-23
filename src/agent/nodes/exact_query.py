import json
import logging
import re
import sqlite3
from difflib import SequenceMatcher
from time import perf_counter
from typing import Dict, List, Optional

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

from src.mappings.company_stock_code_array import CompanyStockCodeArray
from src.providers.chat_openAI_provider import chat_model, get_message_text
from src.services.account_title_matcher import find_candidates, search_item_source_paths
from src.types.langgraph_state_types import OverallState
from src.services.vector_candidate_search import vector_find_candidates
from src.services.concept_map import lookup_concept as _concept_map_lookup


logger = logging.getLogger(__name__)
DB_PATH = "FinancialStatementXBRL.db"
VALID_STATEMENT_TYPES = {
    "balance_sheet",
    "comprehensive_income_statement",
    "statement_of_cash_flows",
}
METADATA_FIELD_PREFIXES = (
    "tifrs-notes_Company",
    "tifrs-notes_Year",
    "tifrs-notes_Quarter",
    "tifrs-notes_Report",
    "tifrs-notes_Market",
    "tifrs-notes_Industry",
)

# ── Confidence threshold ───────────────────────────────────────
# Candidates below this score are excluded before selection.
# Prevents wrong matches like 避險之金融資產 (score 23) from winning
# over 現金及約當現金 (correct concept) in Q1.
MIN_CANDIDATE_SCORE = 45.0

# If top Chinese-matched candidate has score >= this,
# trust it directly without calling LLM — saves one LLM call per field
CHINESE_TRUST_SCORE = 50.0


class Period(BaseModel):
    year: int = Field(..., description="使用者提問中提到的年度")
    quarter: int = Field(..., description="使用者提問中提到的季度，Q1 只回傳 1")
    range: Optional[str] = Field(None, description="季度期間文字，例如 2024年Q1到Q3")


class RequestedField(BaseModel):
    field: str = Field(..., description="使用者請求的會計項目名稱，例如 現金及約當現金")
    category: Optional[str] = Field(
        None,
        description="項目所屬的報表類別，例如 資產負債表、綜合損益表、現金流量表",
    )


class QuestionSchema(BaseModel):
    companyName: str = Field(description="使用者提問中提到的公司全名")
    companyCode: str = Field(description="公司股票代碼")
    shortName: str = Field(description="公司常用簡稱")
    englishName: str = Field(description="公司英文名稱簡寫")
    period: Period
    requested_fields: List[RequestedField]


def dump_log_payload(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def summarize_candidates(candidates: List[Dict], limit: Optional[int] = None) -> List[Dict]:
    items = candidates[:limit] if limit is not None else candidates
    return [
        {
            "concept_name": candidate.get("concept_name"),
            "statement_type": candidate.get("statement_type"),
            "code": candidate.get("code"),
            "zh_tw": candidate.get("zh_tw"),
            "en": candidate.get("en"),
            "mapping_canonical_zh": candidate.get("mapping_canonical_zh"),
            "mapping_canonical_en": candidate.get("mapping_canonical_en"),
            "mapping_aliases": candidate.get("mapping_aliases", [])[:8],
            "score": candidate.get("score"),
            "score_breakdown": candidate.get("score_breakdown"),
            "mapped_from": candidate.get("mapped_from"),
            "mapping_queries": candidate.get("mapping_queries"),
            "dictionary_sources": candidate.get("dictionary_sources", []),
        }
        for candidate in items
    ]


def build_company_maps() -> tuple[Dict[str, Dict], Dict[str, Dict]]:
    code_to_company_map = {}
    name_to_company_map = {}
    for item in CompanyStockCodeArray:
        code_to_company_map[item["companyCode"]] = item
        for key in ["companyName", "shortName", "englishName"]:
            value = item.get(key)
            if value:
                name_to_company_map[value] = item
    return code_to_company_map, name_to_company_map


def resolve_company(schema: Dict) -> Dict:
    code_to_company_map, name_to_company_map = build_company_maps()
    company_identifiers = [
        schema.get("companyCode"),
        schema.get("companyName"),
        schema.get("shortName"),
        schema.get("englishName"),
    ]

    for index, identifier in enumerate(company_identifiers):
        if not identifier:
            continue
        found_company = (
            code_to_company_map.get(identifier)
            if index == 0
            else name_to_company_map.get(identifier)
        )
        if found_company is None:
            matches = [
                item
                for item in CompanyStockCodeArray
                if any(
                    isinstance(value, str) and identifier in value
                    for value in item.values()
                )
            ]
            found_company = matches[0] if matches else None
        if found_company:
            schema["companyName"] = found_company["companyName"]
            schema["companyCode"] = found_company["companyCode"]
            schema["shortName"] = found_company["shortName"]
            schema["englishName"] = found_company["englishName"]
            return schema
    return schema


def resolve_company_profile(company_code: str) -> Dict[str, Optional[str]]:
    profile = {
        "industry_type": None,
        "report_scope": None,
        "report_id_pattern": None,
    }
    if not company_code:
        return profile

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT industry_type, report_scope, report_id
            FROM report_instance
            WHERE company_code = ?
            ORDER BY year DESC, quarter DESC
            LIMIT 1
            """,
            (company_code,),
        ).fetchone()
        if not row:
            return profile

        report_id = row["report_id"]
        report_id_pattern = None
        if report_id and company_code in report_id:
            report_id_pattern = report_id.replace(company_code, "{company_code}")

        report_scope = row["report_scope"]
        if not report_scope and report_id:
            report_id_lower = report_id.lower()
            for scope in ("cr", "er", "ir", "sr"):
                if f"-{scope}-" in report_id_lower:
                    report_scope = scope.upper()
                    break

        return {
            "industry_type": row["industry_type"],
            "report_scope": report_scope,
            "report_id_pattern": report_id_pattern,
        }
    finally:
        connection.close()


def _build_company_lookup():
    """Build company name/code → company dict for fast lookup."""
    lookup = {}
    for item in CompanyStockCodeArray:
        for field in ["companyCode", "companyName", "shortName", "englishName"]:
            val = item.get(field, "").strip()
            if val:
                lookup[val] = item
                if "台" in val:
                    lookup[val.replace("台", "臺")] = item
                if "臺" in val:
                    lookup[val.replace("臺", "台")] = item
    return lookup

_COMPANY_LOOKUP = _build_company_lookup()

def _extract_company_from_text(text: str) -> Optional[Dict]:
    """Rule-based company extraction from question text."""
    # Remove year/quarter patterns before matching to avoid 2024 → companyCode
    clean_text = re.sub(r"20\d{2}\s*年", "", text)
    clean_text = re.sub(r"[Qq][1-4]", "", clean_text)
    clean_text = re.sub(r"第[一二三四]季", "", clean_text)
    # Try direct lookup first (longest match wins)
    best = None
    best_len = 0
    for name, item in _COMPANY_LOOKUP.items():
        if name in clean_text and len(name) > best_len:
            best = item
            best_len = len(name)
    return best

def _extract_period_from_text(text: str) -> Dict:
    """Rule-based year/quarter extraction."""
    year = None
    quarter = None
    # Year: 2024年, 2023年, etc.
    year_match = re.search(r"(20\d{2})\s*年", text)
    if year_match:
        year = int(year_match.group(1))
    # Quarter: Q1, Q2, Q3, Q4, 第一季, 第二季, etc.
    q_match = re.search(r"Q([1-4])", text, re.IGNORECASE)
    if q_match:
        quarter = int(q_match.group(1))
    else:
        chi_q = {"第一季": 1, "第二季": 2, "第三季": 3, "第四季": 4,
                 "一季": 1, "二季": 2, "三季": 3, "四季": 4}
        for kw, q in chi_q.items():
            if kw in text:
                quarter = q
                break
    return {"year": year, "quarter": quarter, "range": None}

def _extract_fields_from_text(text: str) -> List[Dict]:
    """Rule-based financial field extraction from question text."""
    # Known financial field patterns (order matters — longer first)
    FIELD_PATTERNS = [
        "現金及約當現金", "現金水位", "現金部位", "應收帳款淨額", "應收帳款", "應收票據",
        "存貨", "流動資產", "非流動資產", "資產總額", "資產總計", "總資產",
        "流動負債", "非流動負債", "負債總額", "負債總計", "總負債",
        "權益總額", "權益總計", "股東權益",
        "營業收入", "營收", "毛利", "毛利率",
        "營業費用", "營業利益", "營業利潤",
        "稅前淨利", "稅後淨利", "本期淨利", "淨利",
        "每股盈餘", "現金流量", "營業活動現金流",
        "應付帳款", "短期借款", "長期借款",
        "不動產廠房及設備", "無形資產",
    ]
    found = []
    seen = set()
    for pattern in FIELD_PATTERNS:
        if pattern in text and pattern not in seen:
            found.append({"field": pattern, "category": None})
            seen.add(pattern)
    return found

def extract_question_schema(question: str) -> Dict:
    """
    Rule-based question schema extraction — no LLM needed.
    Replaces unreliable Breeze2 JSON parsing with deterministic rules.
    """
    print(f"[exact_query] rule-based extract_question_schema for: {question}")

    # Extract company
    company = _extract_company_from_text(question)
    company_name = company.get("companyName", "") if company else ""
    company_code = company.get("companyCode", "") if company else ""
    short_name = company.get("shortName", "") if company else ""
    english_name = company.get("englishName", "") if company else ""

    # Extract period
    period = _extract_period_from_text(question)

    # Extract fields
    fields = _extract_fields_from_text(question)

    # Fallback to LLM only if rules completely fail
    if not fields:
        print("[exact_query] rule-based: no fields found, falling back to LLM")
        from langchain_core.runnables import RunnableLambda
        parser = JsonOutputParser(pydantic_object=QuestionSchema)
        prompt = PromptTemplate(
            template="""只輸出 JSON，不要任何說明文字。
問題：{question}
格式：{format_instructions}""",
            input_variables=["question"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        def extract_json(message):
            text = message.content if hasattr(message, "content") else str(message)
            text = re.sub(r"```[a-z]*\s*", "", text).strip()
            start = text.find("{")
            if start != -1:
                depth = 0
                for i, ch in enumerate(text[start:], start):
                    if ch == "{": depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return text[start:i+1]
            if "companyName" in text or "CompanyName" in text:
                return "{" + text + "}"
            return text
        try:
            result = (prompt | chat_model | RunnableLambda(extract_json) | parser).invoke({"question": question})
            return result
        except Exception as e:
            print(f"[exact_query] LLM fallback also failed: {e}")
            return {"companyName": "", "companyCode": "", "shortName": "",
                    "englishName": "", "period": period, "requested_fields": []}

    # Normalize field names to match XBRL dictionary zh_names
    FIELD_NORMALIZE = {
        "現金水位": "現金及約當現金",
        "現金部位": "現金及約當現金",
        "負債總額": "負債總計",
        "總負債": "負債總計",
        "資產總額": "資產總計",
        "總資產": "資產總計",
        "權益總額": "權益總計",
    }
    for f in fields:
        if f["field"] in FIELD_NORMALIZE:
            f["field"] = FIELD_NORMALIZE[f["field"]]

    schema = {
        "companyName": company_name,
        "companyCode": company_code,
        "shortName": short_name,
        "englishName": english_name,
        "period": period,
        "requested_fields": fields,
    }
    print(f"[exact_query] rule-based schema: company={company_code} period={period} fields={[f['field'] for f in fields]}")
    return schema


def filter_candidates(candidates: List[Dict]) -> List[Dict]:
    """Remove metadata fields that are never financial values."""
    filtered = []
    for candidate in candidates:
        concept_name = candidate.get("concept_name") or ""
        if any(concept_name.startswith(prefix) for prefix in METADATA_FIELD_PREFIXES):
            continue
        filtered.append(candidate)
    return filtered


def apply_confidence_threshold(candidates: List[Dict]) -> List[Dict]:
    """
    Remove candidates with score below MIN_CANDIDATE_SCORE.
    Directly fixes Q1 problem: 避險之金融資產 scored 23 and ranked #1
    before the correct 現金及約當現金. With threshold=45, it gets excluded.
    If ALL candidates are below threshold, keep top 1 as last resort.
    """
    above = [c for c in candidates if (c.get("score") or 0) >= MIN_CANDIDATE_SCORE]
    if not above and candidates:
        print(
            f"[exact_query] all candidates below threshold {MIN_CANDIDATE_SCORE} "
            f"— keeping top 1 as last resort"
        )
        return candidates[:1]
    return above


def dedupe_candidates(candidates: List[Dict]) -> List[Dict]:
    deduped = {}
    for candidate in candidates:
        concept_name = candidate.get("concept_name")
        statement_type = candidate.get("statement_type")
        key = (concept_name, statement_type)
        if not concept_name:
            continue
        existing = deduped.get(key)
        if existing is None or float(candidate.get("score") or 0) > float(existing.get("score") or 0):
            deduped[key] = candidate
    return list(deduped.values())


def normalize_statement_types(statement_types: List[str]) -> List[str]:
    normalized = []
    for statement_type in statement_types:
        if statement_type in VALID_STATEMENT_TYPES and statement_type not in normalized:
            normalized.append(statement_type)
    return normalized


def normalize_field_name(text: Optional[str]) -> str:
    if not text:
        return ""
    normalized = str(text).strip().lower()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = re.sub(r"[\s、，,／/]+", "", normalized)
    normalized = re.sub(r"[()（）]", "", normalized)
    return normalized


def field_name_match_score(left: Optional[str], right: Optional[str]) -> float:
    left_norm = normalize_field_name(left)
    right_norm = normalize_field_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.92
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def is_chinese(text: str) -> bool:
    """Return True if text contains any Chinese characters."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def get_candidates_with_fallback(
    field_name: str,
    statement_type: str,
    limit: int = 8,
    company_code: Optional[str] = None,
    industry_type: Optional[str] = None,
) -> List[Dict]:
    # ── Concept map fast path ──────────────────────────────────
    # Direct lookup bypasses vector search for known financial terms
    mapped = _concept_map_lookup(field_name)
    if mapped:
        concept_id, mapped_statement_type = mapped
        print(f"[exact_query] concept_map hit for '{field_name}' → {concept_id} ({mapped_statement_type})")
        return [{
            "concept_name": concept_id,
            "statement_type": mapped_statement_type,
            "zh_tw": field_name,
            "en": concept_id.split("_")[-1] if "_" in concept_id else concept_id,
            "code": None,
            "score": 100.0,
            "mapped_from": field_name,
        }]
    """
    Try vector search first (semantic meaning-based matching).
    Fall back to keyword matching if cache is empty or embedding fails.

    Direct fix for Q1 wrong-candidate problem:
      Before: SequenceMatcher scored 避險之金融資產 = 23 (WRONG, ranked #1)
      After:  vector search scores 現金及約當現金 ≈ CashAndCashEquivalents → correct concept ranks first

    Chinese fields benefit most — vector search understands that
    現金及約當現金 and CashAndCashEquivalents mean the same thing.
    """
    try:
        results = vector_find_candidates(
            field_name=field_name,
            statement_type=statement_type,
            limit=limit,
            company_code=company_code,
        )
        if results:
            print(
                f"[exact_query] vector_search hit for '{field_name}' "
                f"statement='{statement_type}' top_score={results[0].get('score', 0):.1f}"
            )
            return results
        print(
            f"[exact_query] vector_search empty for '{field_name}' "
            f"— cache not built? falling back to keyword matching"
        )
    except Exception as exc:
        print(f"[exact_query] vector_search error: {exc} — falling back to keyword matching")

    # Fallback: original keyword/SequenceMatcher approach
    return find_candidates(
        field_name,
        statement_type,
        limit=limit,
        company_code=company_code,
        industry_type=industry_type,
    )


def build_statement_type_candidates(
    field_name: str,
    statement_types: List[str],
    company_code: Optional[str],
    industry_type: Optional[str],
    limit_per_type: int = 8,
) -> List[Dict]:
    """
    Find candidates across all relevant statement types.

    IMPROVED: applies confidence threshold after collecting all candidates.
    This prevents low-score wrong matches from reaching select_candidate().
    """
    normalized_types = normalize_statement_types(statement_types) or list(VALID_STATEMENT_TYPES)
    all_candidates: List[Dict] = []
    candidate_logs: List[Dict] = []

    for statement_type in normalized_types:
        raw_candidates = get_candidates_with_fallback(
            field_name=field_name,
            statement_type=statement_type,
            limit=limit_per_type,
            company_code=company_code,
            industry_type=industry_type,
        )
        candidates = filter_candidates(raw_candidates)

        dictionary_sources = search_item_source_paths(statement_type, company_code)
        candidate_logs.append(
            {
                "field_name": field_name,
                "statement_type": statement_type,
                "dictionary_sources": dictionary_sources,
                "candidate_count": len(candidates),
                "candidates": summarize_candidates(
                    [
                        {
                            **candidate,
                            "statement_type": statement_type,
                            "dictionary_sources": dictionary_sources,
                        }
                        for candidate in candidates
                    ]
                ),
            }
        )
        for candidate in candidates:
            all_candidates.append(
                {
                    **candidate,
                    "statement_type": statement_type,
                    "dictionary_sources": dictionary_sources,
                }
            )

    deduped_candidates = dedupe_candidates(all_candidates)
    deduped_candidates.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            1 if (item.get("concept_name") or "").endswith("Abstract") else 0,
            0 if item.get("code") else 1,
            item.get("statement_type") or "",
            item.get("concept_name") or "",
        )
    )

    # ── Filter: remove candidates semantically mismatched to field_name ──
    # Prevents 資產總計 from being selected for 負債總計 query when both score=100
    def is_semantic_match(candidate: Dict, field: str) -> bool:
        zh = candidate.get("zh_tw") or ""
        concept = candidate.get("concept_name") or ""
        # Hard exclusions: if field contains 負債 but candidate is 資產 (and vice versa)
        if "負債" in field and "資產" in zh and "負債" not in zh:
            return False
        if "資產" in field and "負債" in zh and "資產" not in zh:
            return False
        if "負債" in field and "Equity" in concept and "Liabilit" not in concept:
            return False
        if "權益" in field and "Liabilit" in concept:
            return False
        return True

    deduped_candidates = [c for c in deduped_candidates if is_semantic_match(c, field_name)]

    # ── Apply confidence threshold ─────────────────────────────
    # Remove low-score candidates before they reach LLM selection.
    # Key fix: 避險之金融資產 (score 23) excluded → correct concept wins.
    deduped_candidates = apply_confidence_threshold(deduped_candidates)

    print(
        "[exact_query] candidate_search_by_statement_type:\n"
        + dump_log_payload(candidate_logs)
    )
    print(
        "[exact_query] candidate_pool_after_merge:\n"
        + dump_log_payload(
            {
                "field_name": field_name,
                "requested_statement_types": normalized_types,
                "merged_candidate_count": len(deduped_candidates),
                "candidates": summarize_candidates(deduped_candidates),
            }
        )
    )
    return deduped_candidates


def select_candidate(
    user_question: str,
    field_name: str,
    statement_types: List[str],
    candidates: List[Dict],
) -> Optional[Dict]:
    """
    Select the best candidate for a given field.

    IMPROVED: if the top candidate came from a Chinese query and has
    a good score, trust it directly without calling LLM.
    This saves one LLM call per field for Chinese questions.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    top_candidate = candidates[0]
    second_candidate = candidates[1] if len(candidates) > 1 else None
    top_score = float(top_candidate.get("score") or 0)
    second_score = float(second_candidate.get("score") or 0) if second_candidate else 0.0

    # Auto-select if gap is large
    if top_score >= second_score + 12:
        print(
            f"[exact_query] auto-selected '{top_candidate.get('zh_tw')}' "
            f"score={top_score:.1f} gap={top_score - second_score:.1f} "
            f"for field='{field_name}'"
        )
        return top_candidate

    # ── NEW: trust Chinese-matched top candidate ───────────────
    # If the field_name itself is Chinese and top score is good,
    # skip LLM disambiguation — the Chinese query already found the right concept
    if is_chinese(field_name) and top_score >= CHINESE_TRUST_SCORE:
        print(
            f"[exact_query] trusting Chinese-matched candidate "
            f"'{top_candidate.get('zh_tw')}' score={top_score:.1f} "
            f"for field='{field_name}' — skipping LLM disambiguation"
        )
        return top_candidate

    # LLM disambiguation for ambiguous cases
    compact_candidates = [
        {
            "concept_name": c.get("concept_name"),
            "zh_tw": c.get("zh_tw"),
            "en": c.get("en"),
            "code": c.get("code"),
            "statement_type": c.get("statement_type"),
            "score": c.get("score"),
        }
        for c in candidates[:5]
    ]

    prompt = f"""你是財務欄位選擇器。系統以台灣 IFRS 財務報表為主，查詢語言以繁體中文為主。
請根據使用者問題與查詢欄位，從候選清單中選出最適合查資料的一個 concept_name。
只能回答 concept_name，不要解釋。

### 使用者問題
{user_question}

### 查詢欄位
{field_name}

### 候選清單
{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}
"""
    try:
        print(f"[exact_query] select_candidate LLM prompt for field='{field_name}'")
        response = chat_model.invoke(prompt)
        chosen = get_message_text(response).strip()
        for candidate in candidates:
            if candidate.get("concept_name") == chosen:
                print(
                    f"[exact_query] LLM selected '{candidate.get('zh_tw')}' "
                    f"for field='{field_name}'"
                )
                return candidate
        print(
            f"[exact_query] LLM output '{chosen}' did not match any candidate "
            f"for field='{field_name}' — using top candidate"
        )
    except Exception as exc:
        print(f"[exact_query] select_candidate LLM error: {exc} — using top candidate")

    return top_candidate


class SelectedCandidateSchema(BaseModel):
    concept_name: str = Field(description="從候選清單中選出的 concept_name")
    statement_type: str = Field(description="該 concept_name 對應的報表類型")


def fetch_financial_value(
    company_code: str,
    year: int,
    quarter: int,
    statement_type: str,
    concept_id: str,
    industry_type: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    if statement_type not in VALID_STATEMENT_TYPES:
        return None

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        query = """
        SELECT
            ri.report_id,
            ri.company_code,
            ri.year,
            ri.quarter,
            ri.report_scope,
            ri.industry_type,
            ri.period_end AS report_period_end,
            fd.field_id,
            fd.canonical_name,
            fd.zh_name,
            fd.en_name,
            fd.statement_type,
            fmv.concept_id,
            fmv.value,
            xf.value_numeric,
            xf.value_text,
            xf.unit_id,
            xf.instant_date,
            xf.period_start,
            xf.period_end,
            xf.segment_json
        FROM financial_metric_value AS fmv
        JOIN report_instance AS ri
          ON ri.report_id = fmv.report_id
        LEFT JOIN field_dictionary AS fd
          ON fd.field_id = fmv.field_id
        LEFT JOIN xbrl_fact AS xf
          ON xf.fact_id = fmv.fact_id
        WHERE ri.company_code = ?
          AND ri.year = ?
          AND ri.quarter = ?
          AND fmv.concept_id = ?
          AND fmv.value IS NOT NULL
          AND (fd.statement_type = ? OR fd.statement_type IS NULL)
          AND (
                (fd.statement_type = 'balance_sheet' AND xf.instant_date = ri.period_end)
             OR (fd.statement_type IN ('comprehensive_income_statement', 'statement_of_cash_flows') AND xf.period_end = ri.period_end)
             OR (fd.statement_type IS NULL AND (xf.instant_date = ri.period_end OR xf.period_end = ri.period_end))
          )
        ORDER BY
            CASE
                WHEN ? IS NOT NULL AND ri.industry_type = ? THEN 0
                WHEN ? IS NOT NULL AND (ri.industry_type IS NULL OR ri.industry_type = '') THEN 1
                ELSE 2
            END,
            CASE WHEN xf.segment_json IS NULL THEN 0 ELSE 1 END,
            CASE WHEN xf.unit_id = 'TWD' THEN 0 ELSE 1 END,
            ABS(fmv.value) DESC
        LIMIT 1
        """
        params = (
            company_code,
            year,
            f"Q{quarter}",
            concept_id,
            statement_type,
            industry_type,
            industry_type,
            industry_type,
        )
        row = connection.execute(query, params).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def resolve_answer_data(
    schema: Dict,
    company_profile: Dict[str, Optional[str]],
    selected_candidate: Dict,
    candidates: List[Dict],
) -> tuple[Optional[Dict], Dict, List[Dict]]:
    ordered_candidates = [selected_candidate] + [
        item
        for item in candidates
        if (
            item.get("concept_name") != selected_candidate.get("concept_name")
            or item.get("statement_type") != selected_candidate.get("statement_type")
        )
    ]
    attempt_logs: List[Dict] = []
    for candidate in ordered_candidates:
        answer_data = fetch_financial_value(
            company_code=schema["companyCode"],
            year=schema["period"]["year"],
            quarter=schema["period"]["quarter"],
            statement_type=candidate["statement_type"],
            concept_id=candidate["concept_name"],
            industry_type=company_profile.get("industry_type"),
        )
        if answer_data:
            return answer_data, candidate, attempt_logs

    print(
        f"========No Answer Data Found for field "
        f"'{schema.get('requested_fields', [{}])[0].get('field')}' "
        f"with candidates:\n{dump_log_payload(ordered_candidates)}"
    )
    return None, selected_candidate, attempt_logs


def exact_query(state: OverallState) -> OverallState:
    started_at = perf_counter()
    logger.info("[exact_query] input state:\n%s", dump_log_payload(state))

    step_started_at = perf_counter()
    schema = extract_question_schema(state["user_input"])
    schema = resolve_company(schema)
    company_profile = resolve_company_profile(schema.get("companyCode", ""))
    print(
        f"[timing] exact_query.extract_question_schema_and_resolve_company "
        f"took {perf_counter() - step_started_at:.3f}s"
    )

    requested_fields = schema.get("requested_fields", [])
    if not requested_fields:
        return {
            **state,
            "answer": "無法從問題中辨識要查詢的財務欄位。",
            "retrieved_sources": ["FinancialStatementXBRL.db"],
        }

    # If no quarter specified, expand to all 4 quarters for trend analysis
    if schema.get("period", {}).get("quarter") is None and schema.get("period", {}).get("year"):
        year = schema["period"]["year"]
        schema["_all_quarters"] = True
        print(f"[exact_query] no quarter specified — will fetch all quarters for {year}")

    state_statement_types = normalize_statement_types(state.get("statement_types", []))
    statement_type_result = state.get("statement_type_result", {})
    field_mapping_list = statement_type_result.get("field_mappings", [])

    def resolve_field_statement_types(field_name: str) -> List[str]:
        best_mapping = None
        best_score = 0.0
        for mapping in field_mapping_list:
            score = field_name_match_score(mapping.get("field_name"), field_name)
            if score > best_score:
                best_mapping = mapping
                best_score = score
            if score >= 0.999:
                types = normalize_statement_types(mapping.get("statement_types", []))
                primary = mapping.get("primary_statement_type")
                if primary in VALID_STATEMENT_TYPES and primary not in types:
                    types.insert(0, primary)
                print(
                    "[exact_query] resolve_field_statement_types matched_mapping:\n"
                    + dump_log_payload(
                        {
                            "target_field": field_name,
                            "matched_field_name": mapping.get("field_name"),
                            "match_mode": "exact_normalized",
                            "match_score": round(score, 3),
                            "statement_types": types,
                            "primary_statement_type": primary,
                        }
                    )
                )
                return types
        if best_mapping and best_score >= 0.72:
            types = normalize_statement_types(best_mapping.get("statement_types", []))
            primary = best_mapping.get("primary_statement_type")
            if primary in VALID_STATEMENT_TYPES and primary not in types:
                types.insert(0, primary)
            print(
                "[exact_query] resolve_field_statement_types matched_mapping:\n"
                + dump_log_payload(
                    {
                        "target_field": field_name,
                        "matched_field_name": best_mapping.get("field_name"),
                        "match_mode": "best_fuzzy_match",
                        "match_score": round(best_score, 3),
                        "statement_types": types,
                        "primary_statement_type": primary,
                    }
                )
            )
            return types
        if state.get("statement_type") in VALID_STATEMENT_TYPES:
            print(
                "[exact_query] resolve_field_statement_types fallback:\n"
                + dump_log_payload(
                    {
                        "target_field": field_name,
                        "fallback_mode": "state.statement_type",
                        "statement_types": [state["statement_type"]],
                    }
                )
            )
            return [state["statement_type"]]
        if state_statement_types:
            print(
                "[exact_query] resolve_field_statement_types fallback:\n"
                + dump_log_payload(
                    {
                        "target_field": field_name,
                        "fallback_mode": "state.statement_types",
                        "statement_types": state_statement_types,
                    }
                )
            )
            return state_statement_types
        print(
            "[exact_query] resolve_field_statement_types fallback:\n"
            + dump_log_payload(
                {
                    "target_field": field_name,
                    "fallback_mode": "all_valid_statement_types",
                    "statement_types": list(VALID_STATEMENT_TYPES),
                }
            )
        )
        return list(VALID_STATEMENT_TYPES)

    field_results = []
    unresolved_fields = []

    for requested_field in requested_fields:
        target_field = requested_field["field"]
        target_statement_types = resolve_field_statement_types(target_field)

        step_started_at = perf_counter()
        candidates = build_statement_type_candidates(
            field_name=target_field,
            statement_types=target_statement_types,
            company_code=schema.get("companyCode"),
            industry_type=company_profile.get("industry_type"),
            limit_per_type=8,
        )
        print(
            f"[timing] exact_query.find_candidates took {perf_counter() - step_started_at:.3f}s "
            f"(field={target_field}, statement_types={target_statement_types})"
        )

        if not candidates:
            unresolved_fields.append(
                {
                    "field": target_field,
                    "statement_types": target_statement_types,
                    "reason": "找不到對應的資料字典欄位",
                }
            )
            continue

        step_started_at = perf_counter()
        selected_candidate = select_candidate(
            user_question=state["user_input"],
            field_name=target_field,
            statement_types=target_statement_types,
            candidates=candidates,
        )
        print(
            f"[timing] exact_query.select_candidate took {perf_counter() - step_started_at:.3f}s "
            f"(field={target_field})"
        )
        if not selected_candidate:
            unresolved_fields.append(
                {
                    "field": target_field,
                    "statement_types": target_statement_types,
                    "reason": "無法判斷對應的財報欄位",
                }
            )
            continue

        step_started_at = perf_counter()
        # If no quarter specified, fetch all 4 quarters
        if schema.get("_all_quarters"):
            all_quarter_data = []
            for q in [1, 2, 3, 4]:
                q_schema = {**schema, "period": {**schema["period"], "quarter": q}}
                q_data, q_candidate, _ = resolve_answer_data(
                    schema=q_schema,
                    company_profile=company_profile,
                    selected_candidate=selected_candidate,
                    candidates=candidates,
                )
                if q_data:
                    all_quarter_data.append({"quarter": q, "data": q_data})
            # Use the first found as answer_data, store all in resolved_candidate
            answer_data = all_quarter_data[0]["data"] if all_quarter_data else None
            resolved_candidate = selected_candidate
            attempt_logs = []
            if all_quarter_data:
                resolved_candidate = {**selected_candidate, "_all_quarter_data": all_quarter_data}
        else:
            answer_data, resolved_candidate, attempt_logs = resolve_answer_data(
                schema=schema,
                company_profile=company_profile,
                selected_candidate=selected_candidate,
                candidates=candidates,
            )
        print(
            f"[timing] exact_query.resolve_answer_data took {perf_counter() - step_started_at:.3f}s "
            f"(field={target_field})"
        )

        debug_payload = {
            "query_context": {
                "company_code": schema["companyCode"],
                "company_name": schema["companyName"],
                "year": schema["period"]["year"],
                "quarter": schema["period"]["quarter"],
                "company_profile": company_profile,
                "statement_types": target_statement_types,
                "target_field": target_field,
                "selected_candidate": {
                    "concept_name": selected_candidate.get("concept_name"),
                    "statement_type": selected_candidate.get("statement_type"),
                    "code": selected_candidate.get("code"),
                    "zh_tw": selected_candidate.get("zh_tw"),
                    "en": selected_candidate.get("en"),
                },
                "candidate_count": len(candidates),
            },
            "candidates": [
                {
                    "concept_name": candidate.get("concept_name"),
                    "statement_type": candidate.get("statement_type"),
                    "code": candidate.get("code"),
                    "zh_tw": candidate.get("zh_tw"),
                    "en": candidate.get("en"),
                    "score": candidate.get("score"),
                }
                for candidate in candidates
            ],
            "attempts": attempt_logs,
        }
        logger.info("[exact_query] lookup debug:\n%s", dump_log_payload(debug_payload))

        field_result = {
            "field": target_field,
            "requested_statement_types": target_statement_types,
            "selected_candidate": resolved_candidate,
            "candidates": candidates,
            "answer_data": answer_data,
            "attempts": attempt_logs,
        }
        field_results.append(field_result)

        if not answer_data:
            unresolved_fields.append(
                {
                    "field": target_field,
                    "statement_types": target_statement_types,
                    "selected_candidate": resolved_candidate,
                    "reason": "已完成欄位比對，但查無主期間資料",
                }
            )

    # Expand multi-quarter results for display
    for item in field_results:
        candidate = item.get("selected_candidate", {})
        all_q = candidate.get("_all_quarter_data", [])
        if all_q:
            item["all_quarter_data"] = all_q

    resolved_field_results = [
        item for item in field_results
        if item.get("answer_data") is not None or item.get("all_quarter_data")
    ]
    if not resolved_field_results:
        return {
            **state,
            "answer": "已完成欄位比對，但目前查無可回覆的財務數值。",
            "retrieved_sources": ["FinancialStatementXBRL.db"],
            "reference_data": {
                "schema": schema,
                "company_profile": company_profile,
                "statement_type_result": statement_type_result,
                "field_results": field_results,
                "unresolved_fields": unresolved_fields,
            },
        }

    final_prompt = f"""你是一個專業的信用徵信團隊助手，系統以台灣 IFRS 財務報表為主。
請根據資料庫查到的財務報表資料直接回答問題。

規則：
1. 若問題一次要求多個欄位，請逐項列出。
2. 若答案為數字，請保留正負號，加入千分位格式，並帶出單位。
3. 若該欄位中文名稱存在，優先用中文欄位名稱表達。
4. 若有些欄位查不到，請簡短註明哪些欄位查無主期間資料。
5. 不要臆測，僅根據提供資料回答。
6. 請用自然、口語化的繁體中文方式回答，像一位專業助手在說話。
7. 絕對不可在回答中顯示任何 XBRL 代碼、concept_name 或技術欄位 ID。
   例如 ifrs-full:CashAndCashEquivalents、tifrs-bsci-ci_xxx 這類格式絕對不能出現在回答中。
8. 不要使用 backtick (`) 包住任何文字。
9. 避免過度條列式格式，用自然段落回答。

### 問題
{state['user_input']}

### 查詢條件
公司：{schema['companyName']} ({schema['companyCode']})
期間：{schema['period']['year']} 年 Q{schema['period']['quarter']}
公司申報分類：{dump_log_payload(company_profile)}
整體報表分類：{state_statement_types or statement_type_result.get('statement_types', [])}

### 各欄位查詢結果
{dump_log_payload([
    {**r, "selected_candidate": {
        **r["selected_candidate"],
        "_all_quarter_data": r["selected_candidate"].get("_all_quarter_data", [])
    }} for r in resolved_field_results
])}

### 未查得欄位
{dump_log_payload(unresolved_fields)}
"""

    print("[exact_query] final_answer prompt:\n" + final_prompt)
    step_started_at = perf_counter()
    final_answer = get_message_text(chat_model.invoke(final_prompt))
    print(f"[timing] exact_query.final_answer_generation took {perf_counter() - step_started_at:.3f}s")
    print("[exact_query] final_answer:\n" + str(final_answer))
    print(f"[timing] exact_query.total took {perf_counter() - started_at:.3f}s")

    return {
        **state,
        "answer": final_answer,
        "reference_data": {
            "schema": schema,
            "company_profile": company_profile,
            "statement_type_result": statement_type_result,
            "field_results": field_results,
            "resolved_field_results": resolved_field_results,
            "unresolved_fields": unresolved_fields,
        },
    }
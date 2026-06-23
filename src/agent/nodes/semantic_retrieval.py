import json
import logging
import re
import sqlite3
from time import perf_counter
from typing import Dict, List, Literal, Optional

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field, field_validator

from src.mappings.company_stock_code_array import CompanyStockCodeArray
from src.providers.chat_openAI_provider import chat_model, get_message_text
from src.services.account_title_matcher import find_candidates
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
# Candidates with score below this are excluded before LLM sees them.
# Prevents wrong fields like EBT→FinanceCosts (score 19) from polluting results.
MIN_CANDIDATE_SCORE = 45.0

# If top Chinese-matched candidate has score >= this, trust it directly
# without calling LLM for disambiguation — saves LLM calls
CHINESE_TRUST_SCORE = 50.0


class PeriodItem(BaseModel):
    year: int = Field(..., description="西元年")
    quarter: Optional[int] = Field(None, description="季度，1 到 4；若是全年或未明確指定，可為空")


class RequirementDraft(BaseModel):
    field_query: List[str] = Field(
        default_factory=list,
        description="要查的財務欄位陣列，例如 ['營業收入', '營收']、['資產總額']、['本期淨利', '稅後淨利']",
    )
    statement_type: str = Field(
        ...,
        description="只可填 balance_sheet、comprehensive_income_statement、statement_of_cash_flows",
    )
    periods: List[PeriodItem] = Field(default_factory=list, description="此欄位需要查的期間")
    purpose: str = Field(..., description="查這些數據是為了回答什麼，例如比較、趨勢、計算差異")

    @field_validator("field_query", mode="before")
    @classmethod
    def normalize_field_query(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []


class SemanticPlanDraft(BaseModel):
    company_identifier: str = Field(..., description="公司代碼、公司全名、簡稱或英文名")
    company_identifiers: List[str] = Field(
        default_factory=list,
        description="可用於本地公司清單比對的公司識別候選值，例如公司代碼、公司全名、簡稱、英文名",
    )
    analysis_goal: str = Field(..., description="對問題的高層理解，例如比較營收趨勢、分析獲利變化")
    requirements: List[RequirementDraft] = Field(default_factory=list, description="回答所需的資料清單")

    @field_validator("company_identifiers", mode="before")
    @classmethod
    def normalize_company_identifiers(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []


class CandidateChoiceItem(BaseModel):
    field_query: str = Field(..., description="對應的查詢欄位")
    concept_name: str = Field(..., description="選中的 concept_name")


class CandidateChoiceBatch(BaseModel):
    choices: List[CandidateChoiceItem] = Field(default_factory=list, description="每個 field_query 對應的最佳候選")


def dump_log_payload(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def sanitize_llm_text(text: str) -> str:
    if not text:
        return ""
    sanitized_chars = []
    for char in str(text):
        codepoint = ord(char)
        if 0xD800 <= codepoint <= 0xDFFF:
            continue
        if char in "\n\r\t" or codepoint >= 32:
            sanitized_chars.append(char)
    return "".join(sanitized_chars)


def normalize_periods(periods: List[Dict]) -> List[Dict]:
    normalized_periods = []
    seen = set()
    for period in periods or []:
        if not isinstance(period, dict):
            continue
        year = period.get("year")
        quarter = period.get("quarter")
        try:
            year = int(year)
        except (TypeError, ValueError):
            continue
        try:
            quarter = int(quarter) if quarter is not None else None
        except (TypeError, ValueError):
            quarter = None
        if quarter not in {1, 2, 3, 4}:
            quarter = None
        key = (year, quarter)
        if key in seen:
            continue
        seen.add(key)
        normalized_periods.append({"year": year, "quarter": quarter})
    return normalized_periods


def normalize_semantic_plan(plan: Dict) -> Dict:
    normalized_plan = dict(plan or {})
    company_identifiers = normalized_plan.get("company_identifiers") or []
    if isinstance(company_identifiers, str):
        company_identifiers = [company_identifiers]
    if not isinstance(company_identifiers, list):
        company_identifiers = []
    company_identifier = normalized_plan.get("company_identifier")
    if company_identifier:
        company_identifiers.insert(0, company_identifier)
    normalized_company_identifiers = []
    seen_company_identifiers = set()
    for item in company_identifiers:
        text = str(item).strip()
        if not text or text in seen_company_identifiers:
            continue
        seen_company_identifiers.add(text)
        normalized_company_identifiers.append(text)
    normalized_plan["company_identifiers"] = normalized_company_identifiers

    normalized_requirements = []
    for requirement in normalized_plan.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        normalized_requirement = dict(requirement)
        normalized_requirement["periods"] = normalize_periods(requirement.get("periods", []))
        normalized_requirements.append(normalized_requirement)
    normalized_plan["requirements"] = normalized_requirements
    return normalized_plan


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


def normalize_company_text(value: object) -> str:
    return str(value or "").strip().lower().replace("台", "臺")


def build_company_identifier_candidates(identifiers: object) -> List[str]:
    raw_identifiers = identifiers if isinstance(identifiers, list) else [identifiers]
    candidates = []
    seen = set()

    def append_candidate(value: object) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        candidates.append(text)

    for identifier in raw_identifiers:
        text = str(identifier or "").strip()
        if not text:
            continue
        append_candidate(text)
        for code in re.findall(r"\b\d{4,6}\b", text):
            append_candidate(code)
        for part in re.split(r"[(),，、/|;；]", text):
            append_candidate(part)
            if "台" in part:
                append_candidate(part.replace("台", "臺"))
            if "臺" in part:
                append_candidate(part.replace("臺", "台"))

    return candidates


def resolve_company(identifiers: object) -> Optional[Dict]:
    candidates = build_company_identifier_candidates(identifiers)
    if not candidates:
        return None

    code_to_company_map, name_to_company_map = build_company_maps()
    for identifier in candidates:
        direct = code_to_company_map.get(identifier) or name_to_company_map.get(identifier)
        if direct:
            return direct

    for identifier in candidates:
        normalized_identifier = normalize_company_text(identifier)
        if not normalized_identifier:
            continue
        for item in CompanyStockCodeArray:
            normalized_values = [
                normalize_company_text(value)
                for value in item.values()
                if isinstance(value, str) and value.strip()
            ]
            if any(normalized_identifier == value for value in normalized_values):
                return item
            if len(normalized_identifier) >= 2 and any(
                normalized_identifier in value or value in normalized_identifier
                for value in normalized_values
            ):
                return item

    # ── Extra: match against companyName directly (handles Breeze2
    #    returning full name like "士林電機" when shortName is "士電") ──
    # Known full-name aliases not in CompanyStockCodeArray
    MANUAL_ALIASES = {
        "雄獅旅行社": "2731",
        "晶華酒店":   "2707",
        "葡萄王生技": "1707",
        "士林電機":   "1503",
    }
    for identifier in candidates:
        code = MANUAL_ALIASES.get(str(identifier or "").strip())
        if code:
            for item in CompanyStockCodeArray:
                if item.get("companyCode") == code:
                    return item

    for identifier in candidates:
        text = str(identifier or "").strip()
        if len(text) < 2:
            continue
        for item in CompanyStockCodeArray:
            company_name = item.get("companyName", "")
            if text in company_name or company_name.startswith(text[:4]):
                return item
    return None


def list_company_reports(company_code: str) -> List[Dict]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(
            """
            SELECT report_id, company_code, year, quarter, report_scope, industry_type, module, period_end
            FROM report_instance
            WHERE company_code = ?
            ORDER BY year DESC, quarter DESC
            """,
            (company_code,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


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
    Prevents wrong matches like EBT→FinanceCosts (score 19) from reaching LLM.
    If ALL candidates are below threshold, keep top 1 as last resort.
    """
    above = [c for c in candidates if (c.get("score") or 0) >= MIN_CANDIDATE_SCORE]
    if not above and candidates:
        print(
            f"[semantic_retrieval] all candidates below threshold {MIN_CANDIDATE_SCORE} "
            f"for '{candidates[0].get('matched_query', '')}' — keeping top 1 as last resort"
        )
        return candidates[:1]
    return above


def dedupe_candidates(candidates: List[Dict], limit: int) -> List[Dict]:
    seen = {}
    for candidate in candidates:
        key = candidate.get("concept_name")
        if key and key not in seen:
            seen[key] = candidate
    items = list(seen.values())
    items.sort(
        key=lambda item: (
            -(item.get("score") or 0),
            item.get("statement_type") or "",
            item.get("code") or "",
            item.get("concept_name") or "",
        )
    )
    return items[:limit]


def is_chinese(text: str) -> bool:
    """Return True if text contains any Chinese characters."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ── IMPROVED: Chinese-first candidate search ───────────────────
def search_candidates_across_statements(
    field_queries: List[str],
    statement_type: str,
    limit: int,
    company_code: Optional[str] = None,
) -> List[Dict]:
    """
    Find XBRL concept candidates using Chinese-first strategy.

    WHY CHINESE-FIRST:
      From Q5 logs — Chinese queries matched correctly every time:
        營業收入 → ifrs-full_Revenue ✅
        現金及約當現金 → ifrs-full_CashAndCashEquivalents ✅
      English abbreviations caused wrong matches:
        EBT → FinanceCosts ❌ (score 19)
        Net sales → UnrealizedProfitLossFromSales ❌ (score 55)
        Turnover → Revenue (lucky guess, score 29)

    STRATEGY:
      1. Run Chinese queries first (vector search → keyword fallback)
      2. Only run English queries if Chinese found NO results at all
      3. Apply confidence threshold — exclude score < 45
      4. This prevents English abbreviations from polluting Chinese results
    """
    target_types = (
        [statement_type]
        if statement_type in VALID_STATEMENT_TYPES
        else sorted(VALID_STATEMENT_TYPES)
    )
    collected: List[Dict] = []

    normalized_queries = [
        q.strip() for q in field_queries
        if isinstance(q, str) and q.strip()
    ]

    # Split into Chinese and English
    chinese_queries = [q for q in normalized_queries if is_chinese(q)]
    english_queries = [q for q in normalized_queries if not is_chinese(q)]

    for current_statement_type in target_types:
        chinese_found_any = False

        # ── Concept map fast path ──────────────────────────────
        for field_query in chinese_queries:
            mapped = _concept_map_lookup(field_query)
            if mapped:
                concept_id, mapped_st = mapped
                use_st = mapped_st if current_statement_type not in VALID_STATEMENT_TYPES else current_statement_type
                print(f"[semantic_retrieval] concept_map hit: '{field_query}' → {concept_id}")
                collected.append({
                    "concept_name": concept_id,
                    "statement_type": mapped_st,
                    "zh_tw": field_query,
                    "en": concept_id.split("_")[-1] if "_" in concept_id else concept_id,
                    "code": None,
                    "score": 100.0,
                    "matched_query": field_query,
                    "query_language": "zh",
                    "mapped_from": field_query,
                })
                chinese_found_any = True

        # ── Step 1: Chinese queries first ─────────────────────
        for field_query in chinese_queries:
            candidates_found = False

            # Try vector search first
            try:
                vector_results = vector_find_candidates(
                    field_name=field_query,
                    statement_type=current_statement_type,
                    limit=limit,
                    company_code=company_code,
                )
                if vector_results:
                    candidates_found = True
                    chinese_found_any = True
                    print(
                        f"[semantic_retrieval] vector_search hit (zh): "
                        f"field='{field_query}' statement='{current_statement_type}' "
                        f"top_score={vector_results[0].get('score', 0):.1f}"
                    )
                    for item in filter_candidates(vector_results):
                        enriched = dict(item)
                        enriched["statement_type"] = current_statement_type
                        enriched["matched_query"] = field_query
                        enriched["query_language"] = "zh"
                        collected.append(enriched)
                else:
                    print(
                        f"[semantic_retrieval] vector_search empty for zh '{field_query}' "
                        f"— falling back to keyword matching"
                    )
            except Exception as exc:
                print(
                    f"[semantic_retrieval] vector_search error for '{field_query}': "
                    f"{exc} — falling back to keyword matching"
                )

            # Keyword fallback for Chinese
            if not candidates_found:
                kw_results = filter_candidates(
                    find_candidates(
                        field_query,
                        current_statement_type,
                        limit=limit,
                        company_code=company_code,
                    )
                )
                if kw_results:
                    chinese_found_any = True
                for item in kw_results:
                    enriched = dict(item)
                    enriched["statement_type"] = current_statement_type
                    enriched["matched_query"] = field_query
                    enriched["query_language"] = "zh"
                    collected.append(enriched)

        # ── Step 2: English only if Chinese found nothing ──────
        # This is the key fix — EBT/EBIT/Turnover never run when
        # a good Chinese match already exists
        if not chinese_found_any:
            print(
                f"[semantic_retrieval] Chinese queries found nothing for "
                f"statement='{current_statement_type}' — trying English fallback"
            )
            for field_query in english_queries:
                candidates_found = False
                try:
                    vector_results = vector_find_candidates(
                        field_name=field_query,
                        statement_type=current_statement_type,
                        limit=limit,
                        company_code=company_code,
                    )
                    if vector_results:
                        candidates_found = True
                        print(
                            f"[semantic_retrieval] vector_search hit (en): "
                            f"field='{field_query}' statement='{current_statement_type}' "
                            f"top_score={vector_results[0].get('score', 0):.1f}"
                        )
                        for item in filter_candidates(vector_results):
                            enriched = dict(item)
                            enriched["statement_type"] = current_statement_type
                            enriched["matched_query"] = field_query
                            enriched["query_language"] = "en"
                            collected.append(enriched)
                except Exception as exc:
                    print(
                        f"[semantic_retrieval] vector_search error for en '{field_query}': {exc}"
                    )

                if not candidates_found:
                    for item in filter_candidates(
                        find_candidates(
                            field_query,
                            current_statement_type,
                            limit=limit,
                            company_code=company_code,
                        )
                    ):
                        enriched = dict(item)
                        enriched["statement_type"] = current_statement_type
                        enriched["matched_query"] = field_query
                        enriched["query_language"] = "en"
                        collected.append(enriched)

    # Apply confidence threshold before returning
    deduped = dedupe_candidates(collected, limit * 2)
    filtered_by_score = apply_confidence_threshold(deduped)
    return dedupe_candidates(filtered_by_score, limit)


def fetch_financial_value(
    company_code: str,
    year: int,
    quarter: Optional[int],
    statement_type: str,
    concept_id: str,
) -> Optional[Dict]:
    if statement_type not in VALID_STATEMENT_TYPES:
        return None
    # For annual queries (quarter=None), try Q4 first (year-end), then Q1-Q3
    quarters_to_try = [4, 3, 2, 1] if quarter is None else [quarter]

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(
            """
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
                CASE WHEN xf.segment_json IS NULL THEN 0 ELSE 1 END,
                CASE WHEN xf.unit_id = 'TWD' THEN 0 ELSE 1 END,
                CASE WHEN xf.period_start IS NOT NULL AND xf.period_end IS NOT NULL
                     AND (julianday(xf.period_end) - julianday(xf.period_start)) <= 100
                     THEN 0 ELSE 1 END,
                ABS(fmv.value) DESC
            LIMIT 1
            """,
            (company_code, year, f"Q{quarters_to_try[0]}", concept_id, statement_type),
        )
        row = cursor.fetchone()
        result = dict(row) if row else None
        if result is not None:
            result["requested_year"] = year
            result["requested_quarter"] = quarter
            result["requested_period_type"] = "annual" if quarter is None else "quarterly"
        return result
    finally:
        connection.close()


def extract_semantic_plan(question: str) -> Dict:
    parser = JsonOutputParser(pydantic_object=SemanticPlanDraft)
    sanitized_question = sanitize_llm_text(question)
    prompt = PromptTemplate(
        # ── CHANGED: Rule 12 updated to Chinese-first field_query strategy ──
        template="""你是財務資料需求規劃器。
            你的任務是先判斷：要回答使用者問題，至少需要哪些財務數據。

            規則：
            1. company_identifier 一定要填最適合代表公司的單一識別值，優先使用公司代碼，其次公司簡稱、公司全名或英文名。
            2. company_identifiers 必須填字串陣列，將問題中的公司資訊拆成可供本地公司清單比對的候選值。
               - 若問題包含「台灣水泥 (台泥, Taiwan Cement Corporation, 1101)」，請拆成 ["1101", "台泥", "台灣水泥", "Taiwan Cement Corporation"]。
               - 不要只輸出整段複合描述；必須把代碼、簡稱、全名、英文名分開放入陣列。
               - 若有公司代碼，company_identifier 優先填公司代碼。
            3. statement_type 只能填：
            - balance_sheet
            - comprehensive_income_statement
            - statement_of_cash_flows
            4. requirements 要列出回答此題真正需要查的欄位。
            5. 每個 requirement 的 field_query 必須是字串陣列，規則如下：
               - 第一個必須是最精確的繁體中文欄位名稱，例如「營業利益」「稅前淨利」「現金及約當現金」
               - 第二、三個是繁體中文近義詞或常見別名，例如「稅前損益」「稅前盈餘」「約當現金」
               - 第四個才是對應的英文全名，例如「Operating income」「Cash and cash equivalents」
               - 絕對不要加入英文縮寫（EBT、EPS、EBIT、ROE、ROA），這些容易造成錯誤比對
               - 絕對不要加入過於口語或模糊的英文，例如「Turnover」「Net sales」「Earnings」
               - 每個 requirement 的 field_query 以 4 個為上限
            6. periods 只填問題中明確提到、或回答此題必要的期間。
            7. 如果問題需要比較多個期間，就列出多個 periods。
            8. 若沒有辦法判斷，requirements 仍盡量列出最可能需要的欄位。
            9. 只輸出 JSON，不要輸出 markdown、說明文字或程式碼區塊。
            10. 若問題只提到年份、年度、全年、整年、年增、年度比較，且沒有明確指定 Q1~Q4，periods 中的 quarter 必須填 null，表示要查該年度全年資料。
            11. 只有在問題明確指定季度時，quarter 才能填 1 到 4。

            問題：{question}

            請輸出以下 JSON 格式：
            {{
              "company_identifier": "優先填公司代碼，否則填公司名稱、簡稱或英文名",
              "company_identifiers": ["公司代碼", "公司簡稱", "公司名稱", "公司英文名"],
              "analysis_goal": "這題要分析什麼",
              "requirements": [
                {{
                  "field_query": ["繁體中文欄位名稱", "中文近義詞", "第二中文別名", "English full name"],
                  "statement_type": "balance_sheet 或 comprehensive_income_statement 或 statement_of_cash_flows",
                  "periods": [
                    {{"year": 2024, "quarter": null}},
                    {{"year": 2024, "quarter": 1}}
                  ],
                  "purpose": "查這些欄位的目的"
                }}
              ]
            }}""",
        input_variables=["question"],
    )
    formatted_prompt = sanitize_llm_text(prompt.format(question=sanitized_question))
    print(
        "[semantic_retrieval] extract_semantic_plan request prompt metadata:\n"
        + dump_log_payload(
            {
                "question": sanitized_question,
                "prompt_length": len(formatted_prompt),
                "prompt_preview": formatted_prompt[:2000],
            }
        )
    )
    print("[semantic_retrieval] extract_semantic_plan request prompt full:\n" + formatted_prompt)
    try:
        response = chat_model.invoke(formatted_prompt)
    except Exception as exc:
        print(
            "[semantic_retrieval] extract_semantic_plan request failed:\n"
            + dump_log_payload(
                {
                    "question": sanitized_question,
                    "prompt_length": len(formatted_prompt),
                    "prompt_preview": formatted_prompt[:2000],
                    "error": str(exc),
                }
            )
        )
        raise

    # Robust JSON extraction for Breeze2 compatibility
    raw_text = response.content if hasattr(response, "content") else str(response)
    raw_text = re.sub(r"```[a-z]*\s*", "", raw_text)
    raw_text = re.sub(r"```\s*", "", raw_text).strip()
    # Brace-matching
    start = raw_text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw_text[start:], start):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw_text = raw_text[start:i+1]
                    break
    elif "company_identifier" in raw_text:
        raw_text = "{" + raw_text + "}"

    import json as _json
    try:
        parsed_dict = _json.loads(raw_text)
    except Exception:
        parsed_dict = parser.parse(raw_text)
    if isinstance(parsed_dict, dict):
        semantic_plan = normalize_semantic_plan(parsed_dict)
    else:
        semantic_plan = normalize_semantic_plan(parsed_dict.model_dump() if hasattr(parsed_dict, "model_dump") else {})

    # ── Fix periods: "各季" → Q1~Q4, no quarter → all quarters ──
    question_text = semantic_plan.get("company_identifier", "") + " " + str(semantic_plan.get("analysis_goal", ""))
    original_question = sanitized_question
    needs_all_quarters = any(kw in original_question for kw in ["各季", "每季", "季趨勢", "季營收"])
    for req in semantic_plan.get("requirements", []):
        periods = req.get("periods", [])
        if needs_all_quarters:
            # Replace with explicit Q1-Q4
            years = list({p.get("year") for p in periods if p.get("year")}) or [2024]
            new_periods = []
            for y in years:
                for q in [1, 2, 3, 4]:
                    new_periods.append({"year": y, "quarter": q})
            req["periods"] = new_periods
            print(f"[semantic_retrieval] expanded periods to all quarters: {new_periods}")
        elif periods and all(p.get("quarter") is None for p in periods):
            # Annual query — add Q1-Q4 explicitly
            years = list({p.get("year") for p in periods if p.get("year")}) or [2024]
            new_periods = []
            for y in years:
                for q in [1, 2, 3, 4]:
                    new_periods.append({"year": y, "quarter": q})
            req["periods"] = new_periods
            print(f"[semantic_retrieval] expanded annual periods to quarters: {new_periods}")

    # ── Fix vague field queries from Breeze2 ──────────────────
    FIELD_QUERY_UPGRADES = {
        "負債": ["負債總計", "負債合計", "負債總額", "Total liabilities"],
        "總負債": ["負債總計", "負債合計", "負債總額", "Total liabilities"],
        "資產": ["資產總計", "資產合計", "資產總額", "Total assets"],
        "總資產": ["資產總計", "資產合計", "資產總額", "Total assets"],
        "淨利": ["本期淨利", "本期稅後淨利", "本期損益", "Profit or loss for the period"],
        "稅後淨利": ["本期淨利", "本期稅後淨利", "本期損益", "Profit or loss for the period"],
        "稅前淨利": ["稅前損益", "稅前淨利", "繼續營業單位稅前損益", "Profit before tax"],
        "獲利": ["本期淨利", "本期稅後淨利", "營業利益", "Operating profit"],
        "收入": ["營業收入", "營收", "總收入", "Revenue"],
        "營收": ["營業收入", "本期營業收入", "收入", "Revenue"],
    }
    for req in semantic_plan.get("requirements", []):
        fq = req.get("field_query", [])
        if isinstance(fq, list) and fq:
            first = fq[0] if fq else ""
            if first in FIELD_QUERY_UPGRADES:
                req["field_query"] = FIELD_QUERY_UPGRADES[first]
                print(f"[semantic_retrieval] upgraded field_query '{first}' → {FIELD_QUERY_UPGRADES[first]}")

    return semantic_plan


def choose_best_candidate(question: str, requirement: Dict, candidates: List[Dict]) -> Dict:
    if not candidates:
        return {}
    if len(candidates) == 1:
        return candidates[0]
    top_candidate = candidates[0]
    second_candidate = candidates[1] if len(candidates) > 1 else None
    top_score = float(top_candidate.get("score") or 0)
    second_score = float(second_candidate.get("score") or 0) if second_candidate else 0.0
    if top_score >= second_score + 12:
        return top_candidate

    compact_candidates = [
        {
            "concept_name": candidate.get("concept_name"),
            "zh_tw": candidate.get("zh_tw"),
            "en": candidate.get("en"),
            "code": candidate.get("code"),
            "statement_type": candidate.get("statement_type"),
            "matched_query": candidate.get("matched_query"),
            "score": candidate.get("score"),
        }
        for candidate in candidates[:3]
    ]
    compact_requirement = {
        "field_query": requirement.get("field_query"),
        "statement_type": requirement.get("statement_type"),
        "periods": requirement.get("periods"),
        "purpose": requirement.get("purpose"),
    }

    prompt = sanitize_llm_text(
        f"""
你是財務欄位選擇器。系統以台灣 IFRS 財務報表為主，查詢語言以繁體中文為主。
請根據使用者問題與資料需求，從候選清單中選出最適合查資料的一個 concept_name。
只能回答 concept_name，不要解釋。

### 使用者問題
{question}

### 資料需求
{json.dumps(compact_requirement, ensure_ascii=False, indent=2)}

### 候選清單
{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}
"""
    )
    try:
        print("[semantic_retrieval] choose_best_candidate prompt:\n" + prompt)
        response = chat_model.invoke(prompt)
        chosen = get_message_text(response).strip()
        for candidate in candidates:
            if candidate.get("concept_name") == chosen:
                return candidate
        print(
            "[semantic_retrieval] choose_best_candidate fallback due to unmatched llm output:\n"
            + dump_log_payload(
                {
                    "question": question,
                    "requirement": compact_requirement,
                    "candidates": compact_candidates,
                    "llm_output": chosen,
                }
            )
        )
    except Exception as exc:
        print(f"[semantic_retrieval] choose_best_candidate fallback due to error: {exc}")
    return candidates[0]


def choose_best_candidates_for_requirement(
    question: str,
    requirement: Dict,
    candidates_by_field_query: Dict[str, List[Dict]],
) -> Dict[str, Dict]:
    selected_candidates = {}
    llm_tasks = []

    for field_query, candidates in candidates_by_field_query.items():
        if not candidates:
            selected_candidates[field_query] = {}
            continue
        if len(candidates) == 1:
            selected_candidates[field_query] = candidates[0]
            continue

        top_candidate = candidates[0]
        second_candidate = candidates[1] if len(candidates) > 1 else None
        top_score = float(top_candidate.get("score") or 0)
        second_score = float(second_candidate.get("score") or 0) if second_candidate else 0.0

        # Auto-select if gap is large enough
        if top_score >= second_score + 12:
            selected_candidates[field_query] = top_candidate
            continue

        # ── NEW: Trust Chinese-matched candidates directly ─────
        # If top candidate came from a Chinese query and has good score,
        # skip LLM disambiguation — saves an LLM call per field
        top_matched_query = top_candidate.get("matched_query", "")
        top_is_chinese = is_chinese(top_matched_query)
        if top_is_chinese and top_score >= CHINESE_TRUST_SCORE:
            print(
                f"[semantic_retrieval] trusting Chinese-matched candidate "
                f"'{top_candidate.get('zh_tw')}' score={top_score:.1f} "
                f"for field_query='{field_query}' — skipping LLM disambiguation"
            )
            selected_candidates[field_query] = top_candidate
            continue

        llm_tasks.append(
            {
                "field_query": field_query,
                "candidates": [
                    {
                        "concept_name": candidate.get("concept_name"),
                        "zh_tw": candidate.get("zh_tw"),
                        "en": candidate.get("en"),
                        "code": candidate.get("code"),
                        "statement_type": candidate.get("statement_type"),
                        "matched_query": candidate.get("matched_query"),
                        "score": candidate.get("score"),
                    }
                    for candidate in candidates[:3]
                ],
            }
        )

    if not llm_tasks:
        return selected_candidates

    parser = JsonOutputParser(pydantic_object=CandidateChoiceBatch)
    compact_requirement = {
        "field_query": requirement.get("field_query"),
        "statement_type": requirement.get("statement_type"),
        "periods": requirement.get("periods"),
        "purpose": requirement.get("purpose"),
    }
    prompt = sanitize_llm_text(
        f"""
你是財務欄位選擇器。系統以台灣 IFRS 財務報表為主，查詢語言以繁體中文為主。
請根據使用者問題與資料需求，為每個 field_query 從對應候選清單中選出最適合查資料的一個 concept_name。

規則：
1. 只能從該 field_query 自己的 candidates 中選。
2. 每個 field_query 最多選一個 concept_name。
3. 優先選擇 zh_tw 欄位與 field_query 語意最接近的候選。
4. 只輸出 JSON，不要輸出 markdown、說明文字或程式碼區塊。

### 使用者問題
{question}

### 資料需求
{json.dumps(compact_requirement, ensure_ascii=False, indent=2)}

### 待選欄位與候選清單
{json.dumps(llm_tasks, ensure_ascii=False, indent=2)}

### JSON 格式
{{
  "choices": [
    {{"field_query": "營業收入", "concept_name": "ifrs-full_Revenue"}}
  ]
}}
"""
    )
    try:
        print("[semantic_retrieval] choose_best_candidates_for_requirement prompt:\n" + prompt)
        response = chat_model.invoke(prompt)
        raw_choice = get_message_text(response)
        raw_choice = re.sub(r"```[a-z]*\s*", "", raw_choice)
        raw_choice = re.sub(r"```\s*", "", raw_choice).strip()
        start_c = raw_choice.find("{")
        if start_c != -1:
            depth_c = 0
            for i_c, ch_c in enumerate(raw_choice[start_c:], start_c):
                if ch_c == "{": depth_c += 1
                elif ch_c == "}":
                    depth_c -= 1
                    if depth_c == 0:
                        raw_choice = raw_choice[start_c:i_c+1]
                        break
        import json as _json2
        try:
            parsed_obj = _json2.loads(raw_choice)
            choices_list = parsed_obj.get("choices", []) if isinstance(parsed_obj, dict) else []
        except Exception:
            try:
                parsed_obj = parser.parse(raw_choice)
                choices_list = parsed_obj.get("choices", []) if isinstance(parsed_obj, dict) else []
            except Exception:
                choices_list = []
        choice_map = {
            item.get("field_query"): item.get("concept_name")
            for item in choices_list
            if isinstance(item, dict)
        }
        for task in llm_tasks:
            field_query = task["field_query"]
            chosen = choice_map.get(field_query)
            matched_candidate = next(
                (
                    candidate
                    for candidate in candidates_by_field_query.get(field_query, [])
                    if candidate.get("concept_name") == chosen
                ),
                None,
            )
            if matched_candidate:
                selected_candidates[field_query] = matched_candidate
            else:
                selected_candidates[field_query] = choose_best_candidate(
                    question,
                    {**requirement, "field_query": [field_query]},
                    candidates_by_field_query.get(field_query, []),
                )
        return selected_candidates
    except Exception as exc:
        print(f"[semantic_retrieval] choose_best_candidates_for_requirement fallback due to error: {exc}")
        for task in llm_tasks:
            field_query = task["field_query"]
            selected_candidates[field_query] = choose_best_candidate(
                question,
                {**requirement, "field_query": [field_query]},
                candidates_by_field_query.get(field_query, []),
            )
        return selected_candidates


def build_llm_evidence_candidate(candidate: Dict) -> Dict:
    if not candidate:
        return {}
    return {
        "concept_name": candidate.get("concept_name"),
        "zh_tw": candidate.get("zh_tw"),
        "en": candidate.get("en"),
        "code": candidate.get("code"),
        "statement_type": candidate.get("statement_type"),
        "matched_query": candidate.get("matched_query"),
        "score": candidate.get("score"),
        "mapped_from": candidate.get("mapped_from"),
        "mapping_queries": candidate.get("mapping_queries"),
    }


def build_candidate_score_log(candidate: Dict) -> Dict:
    if not candidate:
        return {}
    return {
        "concept_name": candidate.get("concept_name"),
        "zh_tw": candidate.get("zh_tw"),
        "en": candidate.get("en"),
        "code": candidate.get("code"),
        "statement_type": candidate.get("statement_type"),
        "matched_query": candidate.get("matched_query"),
        "score": candidate.get("score"),
        "mapped_from": candidate.get("mapped_from"),
        "mapping_queries": candidate.get("mapping_queries"),
        "score_breakdown": candidate.get("score_breakdown"),
    }


def print_requirement_candidate_score_log(
    requirement: Dict,
    candidates_by_field_query: Dict[str, List[Dict]],
    selected_candidates_by_field_query: Dict[str, Dict],
    limit: int = 5,
) -> None:
    payload = {
        "requirement": {
            "field_query": requirement.get("field_query"),
            "statement_type": requirement.get("statement_type"),
            "periods": requirement.get("periods"),
            "purpose": requirement.get("purpose"),
        },
        "field_query_matches": [],
    }
    for field_query, candidates in candidates_by_field_query.items():
        selected_candidate = selected_candidates_by_field_query.get(field_query, {})
        payload["field_query_matches"].append(
            {
                "field_query": field_query,
                "selected_candidate": build_candidate_score_log(selected_candidate),
                "top_candidates": [
                    build_candidate_score_log(candidate)
                    for candidate in candidates[:limit]
                ],
            }
        )


def get_fact_label(field_query: str, candidate: Dict) -> str:
    return (
        candidate.get("zh_tw")
        or candidate.get("en")
        or field_query
        or candidate.get("concept_name")
        or "未命名項目"
    )


def normalize_match_text(value: object) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def is_candidate_low_confidence(field_query: str, candidate: Dict) -> bool:
    if not candidate:
        return True

    concept_name = normalize_match_text(candidate.get("concept_name"))
    label_text = normalize_match_text(
        " ".join(
            [
                str(candidate.get("zh_tw") or ""),
                str(candidate.get("en") or ""),
                str(candidate.get("matched_query") or ""),
            ]
        )
    )
    field_text = normalize_match_text(field_query)
    combined_candidate_text = f"{concept_name} {label_text}"

    if any(term in field_text for term in ("liquid assets", "short term assets", "short-term assets")):
        if "assets" == concept_name.split()[-1] or "shorttermborrowings" in concept_name.replace(" ", ""):
            return True
        if "borrowings" in combined_candidate_text:
            return True

    if "current assets total" in field_text:
        if "currentassets" not in concept_name.replace(" ", ""):
            return True

    if "current liabilities" in field_text:
        if "currentliabilities" not in concept_name.replace(" ", ""):
            return True

    if "cash and cash equivalents" in field_text or "現金及約當現金" in field_text:
        if "cashandcashequivalents" not in concept_name.replace(" ", ""):
            return True

    # Exclude Abstract concepts — they never have data
    if concept_name.endswith("abstract"):
        return True

    # Allow P&L concepts through — don't exclude 本期淨利/稅後淨利
    if any(kw in field_text for kw in ("淨利", "profitloss", "profit loss", "net income")):
        if any(kw in concept_name for kw in ("ProfitLoss", "NetIncome", "Profit")):
            return False

    # Exclude obviously wrong matches (薪津 for 淨利 etc)
    label_zh = str(candidate.get("zh_tw") or "")
    if any(kw in field_text for kw in ("淨利", "獲利", "profit")):
        if any(wrong in label_zh for wrong in ("薪津", "薪資", "薪酬", "員工")):
            return True

    if "short term debt" in field_text or "short-term debt" in field_text:
        debt_terms = ("borrowings", "commercialpapers", "notesbillspayable", "shortterm")
        if not any(term in combined_candidate_text.replace(" ", "") for term in debt_terms):
            return True

    return False


def build_compact_fact(
    field_query: str,
    requirement: Dict,
    selected_candidate: Dict,
    value_item: Dict,
) -> Dict:
    result = value_item.get("result") or {}
    period = value_item.get("period") or {}
    return {
        "label": get_fact_label(field_query, selected_candidate),
        "field_query": field_query,
        "concept_name": selected_candidate.get("concept_name"),
        "statement_type": selected_candidate.get("statement_type") or requirement.get("statement_type"),
        "period": {
            "year": result.get("year") or period.get("year"),
            "quarter": result.get("quarter"),
            "report_period_end": result.get("report_period_end"),
        },
        "value": result.get("value_numeric")
        if result.get("value_numeric") is not None
        else result.get("value"),
        "value_text": result.get("value_text"),
        "unit": result.get("unit_id"),
        "purpose": requirement.get("purpose"),
    }


def compact_metric_value(value: float) -> float:
    return round(value, 4)


def find_numeric_fact(facts: List[Dict], concept_names: List[str]) -> Optional[Dict]:
    concept_name_set = set(concept_names)
    for fact in facts:
        if fact.get("concept_name") in concept_name_set and isinstance(fact.get("value"), (int, float)):
            return fact
    return None


def build_computed_metrics(facts: List[Dict]) -> List[Dict]:
    metrics = []
    current_assets = find_numeric_fact(facts, ["ifrs-full_CurrentAssets"])
    current_liabilities = find_numeric_fact(facts, ["ifrs-full_CurrentLiabilities"])
    cash = find_numeric_fact(facts, ["ifrs-full_CashAndCashEquivalents"])
    operating_cash_flow = find_numeric_fact(
        facts,
        [
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "ifrs-full_CashFlowsFromUsedInOperations",
            "tifrs-SCF_CashFlowsFromUsedInOperatingActivities",
        ],
    )
    total_assets = find_numeric_fact(facts, ["ifrs-full_Assets"])
    total_liabilities = find_numeric_fact(facts, ["ifrs-full_Liabilities"])
    total_equity = find_numeric_fact(facts, ["ifrs-full_Equity"])
    revenue = find_numeric_fact(facts, ["ifrs-full_Revenue"])
    net_income = find_numeric_fact(facts, [
        "ifrs-full_ProfitLoss",
        "ifrs-full_ProfitLossFromContinuingOperations",
    ])
    operating_income = find_numeric_fact(facts, ["ifrs-full_ProfitLossFromOperatingActivities"])

    if current_assets and current_liabilities and current_liabilities["value"]:
        metrics.append({
            "label": "流動比率",
            "formula": "流動資產 / 流動負債",
            "value": compact_metric_value(current_assets["value"] / current_liabilities["value"]),
        })

    if cash and current_liabilities and current_liabilities["value"]:
        metrics.append({
            "label": "現金對流動負債比",
            "formula": "現金及約當現金 / 流動負債",
            "value": compact_metric_value(cash["value"] / current_liabilities["value"]),
        })

    if operating_cash_flow and current_liabilities and current_liabilities["value"]:
        metrics.append({
            "label": "營業現金流對流動負債比",
            "formula": "營業活動淨現金流 / 流動負債",
            "value": compact_metric_value(operating_cash_flow["value"] / current_liabilities["value"]),
        })

    # ── NEW: additional financial ratios ──────────────────────
    if total_liabilities and total_assets and total_assets["value"]:
        metrics.append({
            "label": "負債比率",
            "formula": "負債總計 / 資產總計",
            "value": compact_metric_value(total_liabilities["value"] / total_assets["value"]),
        })

    if total_liabilities and total_equity and total_equity["value"]:
        metrics.append({
            "label": "負債權益比",
            "formula": "負債總計 / 權益總計",
            "value": compact_metric_value(total_liabilities["value"] / total_equity["value"]),
        })

    if operating_income and revenue and revenue["value"]:
        metrics.append({
            "label": "營業利益率",
            "formula": "營業利益 / 營業收入",
            "value": compact_metric_value(operating_income["value"] / revenue["value"]),
        })

    if net_income and revenue and revenue["value"]:
        metrics.append({
            "label": "淨利率",
            "formula": "本期淨利 / 營業收入",
            "value": compact_metric_value(net_income["value"] / revenue["value"]),
        })

    return metrics


# ── NEW: accounting cross-validation ──────────────────────────
def validate_facts_accounting_logic(facts: List[Dict]) -> List[str]:
    """
    Check if fetched numbers make basic accounting sense.
    Returns list of validation warning messages.
    Prints warnings to log for debugging.
    """
    warnings = []
    fact_map = {
        f["concept_name"]: f
        for f in facts
        if f.get("concept_name") and f.get("value") is not None
    }

    revenue    = fact_map.get("ifrs-full_Revenue")
    op_cost    = fact_map.get("tifrs-bsci-ci_OperatingCosts")
    gross      = fact_map.get("tifrs-bsci-ci_GrossProfitLossFromOperations")
    op_inc     = fact_map.get("ifrs-full_ProfitLossFromOperatingActivities")
    tax_exp    = fact_map.get("ifrs-full_IncomeTaxExpenseContinuingOperations")
    assets     = fact_map.get("ifrs-full_Assets")
    liab       = fact_map.get("ifrs-full_Liabilities")
    equity     = fact_map.get("ifrs-full_Equity")

    # Rule 1: Gross profit = Revenue - Operating cost (5% tolerance)
    if revenue and op_cost and gross:
        expected = revenue["value"] - op_cost["value"]
        actual = gross["value"]
        if expected != 0 and abs(actual - expected) / abs(expected) > 0.05:
            msg = (
                f"[validation] WARN: 毛利驗證失敗 "
                f"預期={expected:,.0f} 實際={actual:,.0f} "
                f"(誤差 {abs(actual-expected)/abs(expected)*100:.1f}%)"
            )
            print(msg)
            warnings.append(msg)

    # Rule 2: Tax expense should be less than operating income
    if tax_exp and op_inc and op_inc["value"] > 0:
        if abs(tax_exp["value"]) >= abs(op_inc["value"]):
            msg = (
                f"[validation] WARN: 所得稅費用 ({tax_exp['value']:,.0f}) "
                f">= 營業利益 ({op_inc['value']:,.0f}) — 可能抓到錯誤欄位"
            )
            print(msg)
            warnings.append(msg)

    # Rule 3: Assets = Liabilities + Equity (1% tolerance)
    if assets and liab and equity:
        expected = liab["value"] + equity["value"]
        actual = assets["value"]
        if expected != 0 and abs(actual - expected) / abs(expected) > 0.01:
            msg = (
                f"[validation] WARN: 會計恆等式驗證失敗 "
                f"資產={actual:,.0f} 負債+權益={expected:,.0f} "
                f"(誤差 {abs(actual-expected)/abs(expected)*100:.1f}%)"
            )
            print(msg)
            warnings.append(msg)

    if warnings:
        print(f"[validation] {len(warnings)} warning(s) found — check candidate selection above")
    else:
        print("[validation] accounting logic check passed ✅")

    return warnings


def build_final_answer_evidence(
    question: str,
    plan: Dict,
    company: Dict,
    retrieval_results: List[Dict],
) -> Dict:
    facts = []
    excluded_or_low_confidence_facts = []
    fact_keys = set()

    for item in retrieval_results:
        requirement = item.get("requirement", {})
        for query_result in item.get("query_results", []):
            field_query = query_result.get("field_query")
            selected_candidate = query_result.get("selected_candidate", {})
            for value_item in query_result.get("values", []):
                result = value_item.get("result")
                if result is None:
                    excluded_or_low_confidence_facts.append(
                        {
                            "field_query": field_query,
                            "reason": "查無資料庫數值",
                            "selected_candidate": {
                                "concept_name": selected_candidate.get("concept_name"),
                                "zh_tw": selected_candidate.get("zh_tw"),
                                "en": selected_candidate.get("en"),
                            },
                            "period": value_item.get("period"),
                        }
                    )
                    continue

                if is_candidate_low_confidence(field_query, selected_candidate):
                    excluded_or_low_confidence_facts.append(
                        {
                            "field_query": field_query,
                            "reason": "候選欄位與查詢語意可能不一致，未提供給最終回答引用",
                            "selected_candidate": {
                                "concept_name": selected_candidate.get("concept_name"),
                                "zh_tw": selected_candidate.get("zh_tw"),
                                "en": selected_candidate.get("en"),
                            },
                            "value": result.get("value_numeric")
                            if result.get("value_numeric") is not None
                            else result.get("value"),
                            "unit": result.get("unit_id"),
                            "period": {
                                "year": result.get("year"),
                                "quarter": result.get("quarter"),
                                "report_period_end": result.get("report_period_end"),
                            },
                        }
                    )
                    continue

                fact = build_compact_fact(
                    field_query=field_query,
                    requirement=requirement,
                    selected_candidate=selected_candidate,
                    value_item=value_item,
                )
                fact_key = (
                    fact.get("concept_name"),
                    fact.get("statement_type"),
                    fact.get("period", {}).get("year"),
                    fact.get("period", {}).get("quarter"),
                    fact.get("period", {}).get("report_period_end"),
                )
                if fact_key in fact_keys:
                    continue
                fact_keys.add(fact_key)
                facts.append(fact)

    periods = []
    period_keys = set()
    for fact in facts:
        period = fact.get("period") or {}
        period_key = (period.get("year"), period.get("quarter"), period.get("report_period_end"))
        if period_key in period_keys:
            continue
        period_keys.add(period_key)
        periods.append(period)

    computed_metrics = build_computed_metrics(facts)

    return {
        "question": question,
        "analysis_goal": plan.get("analysis_goal"),
        "company": {
            "code": company.get("companyCode"),
            "name": company.get("companyName"),
            "short_name": company.get("shortName"),
            "english_name": company.get("englishName"),
        },
        "periods": periods,
        "facts": facts,
        "computed_metrics": computed_metrics,
        "excluded_or_low_confidence_facts": excluded_or_low_confidence_facts[:20],
    }


def get_log_item_zh_name(detail: Dict) -> str:
    selected_candidate = detail.get("selected_candidate") or {}
    return (
        selected_candidate.get("zh_tw")
        or detail.get("field_query")
        or "未命名項目"
    )


def print_unique_log_item_names(label: str, details: List[Dict]) -> None:
    print(label)
    seen_items = set()
    for detail in details:
        name = get_log_item_zh_name(detail)
        selected_candidate = detail.get("selected_candidate") or {}
        concept_name = selected_candidate.get("concept_name")
        item_key = (name, concept_name)
        if item_key in seen_items:
            continue
        seen_items.add(item_key)
        if concept_name:
            print(f"- {name} ({concept_name})")
        else:
            print(f"- {name}")
    if not seen_items:
        print("- 無")


def retrieve_requirement_data(question: str, company: Dict, requirement: Dict) -> Dict:
    field_queries = requirement.get("field_query", [])
    query_results = []
    values = []
    candidates_by_field_query = {}
    value_result_cache = {}
    emitted_value_keys = set()

    candidate_search_started_at = perf_counter()
    for field_query in field_queries:
        candidates = search_candidates_across_statements(
            field_queries=[field_query],
            statement_type=requirement["statement_type"],
            limit=8,
            company_code=company["companyCode"],
        )
        candidates_by_field_query[field_query] = candidates
    print(
        f"[timing] semantic_retrieval.match_requirement_field_queries took "
        f"{perf_counter() - candidate_search_started_at:.3f}s "
        f"(statement_type={requirement.get('statement_type')}, "
        f"field_queries={len(field_queries)}, "
        f"candidate_count={sum(len(c) for c in candidates_by_field_query.values())})"
    )

    selected_candidates_by_field_query = choose_best_candidates_for_requirement(
        question=question,
        requirement=requirement,
        candidates_by_field_query=candidates_by_field_query,
    )
    print_requirement_candidate_score_log(
        requirement=requirement,
        candidates_by_field_query=candidates_by_field_query,
        selected_candidates_by_field_query=selected_candidates_by_field_query,
    )

    for field_query in field_queries:
        candidates = candidates_by_field_query.get(field_query, [])
        selected_candidate = selected_candidates_by_field_query.get(field_query, {})

        query_values = []
        for period in requirement.get("periods", []):
            result = None
            quarter = period.get("quarter")
            value_key = None
            if selected_candidate:
                statement_type = selected_candidate.get("statement_type") or requirement["statement_type"]
                concept_name = selected_candidate.get("concept_name")
                value_key = (
                    statement_type,
                    concept_name,
                    period.get("year"),
                    quarter,
                )
                if value_key in value_result_cache:
                    result = value_result_cache[value_key]
                else:
                    result = fetch_financial_value(
                        company_code=company["companyCode"],
                        year=period["year"],
                        quarter=quarter,
                        statement_type=statement_type,
                        concept_id=concept_name,
                    )
                    value_result_cache[value_key] = result
            value_item = {
                "field_query": field_query,
                "period": period,
                "result": result,
            }
            if value_key is None or value_key not in emitted_value_keys:
                query_values.append(value_item)
                values.append(value_item)
                if value_key is not None:
                    emitted_value_keys.add(value_key)

        query_results.append(
            {
                "field_query": field_query,
                "selected_candidate": build_llm_evidence_candidate(selected_candidate),
                "candidates": [build_llm_evidence_candidate(c) for c in candidates],
                "values": query_values,
            }
        )

    return {
        "requirement": requirement,
        "query_results": query_results,
        "values": values,
    }


def semantic_retrieval(state: OverallState) -> OverallState:
    print("semantic_retrieval in =======")
    started_at = perf_counter()

    question = state["rephrased_question"] or state["user_input"]
    try:
        step_started_at = perf_counter()
        plan = extract_semantic_plan(question)
        print(f"[timing] semantic_retrieval.extract_semantic_plan took {perf_counter() - step_started_at:.3f}s")
    except Exception as exc:
        return {
            **state,
            "answer": f"語意檢索規劃階段失敗，暫時無法分析所需財務資料。錯誤：{exc}",
            "reference_data": {"question": question},
        }
    print("\n********** [semantic_retrieval] AI AGENT data-requirement plan start **********")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print("********** [semantic_retrieval] AI AGENT data-requirement plan end **********\n")

    step_started_at = perf_counter()
    company = resolve_company(plan.get("company_identifiers") or plan.get("company_identifier", ""))
    print(f"company: {json.dumps(company, ensure_ascii=False, indent=2)}")
    print(f"[timing] semantic_retrieval.resolve_company took {perf_counter() - step_started_at:.3f}s")

    if not company:
        return {
            **state,
            "answer": "當前提供的公司名稱資訊不足，無法匹配到現有台灣公司，請補充該公司完整名稱",
            "reference_data": {"plan": plan},
        }

    step_started_at = perf_counter()
    available_reports = list_company_reports(company["companyCode"])
    print(f"[timing] semantic_retrieval.list_company_reports took {perf_counter() - step_started_at:.3f}s")

    retrieval_results = []
    step_started_at = perf_counter()
    for requirement in plan.get("requirements", []):
        result = retrieve_requirement_data(question, company, requirement)
        retrieval_results.append(result)
    print(f"[timing] semantic_retrieval.retrieve_requirement_data_total took {perf_counter() - step_started_at:.3f}s")

    llm_evidence_json = build_final_answer_evidence(
        question=question,
        plan=plan,
        company=company,
        retrieval_results=retrieval_results,
    )

    # ── NEW: cross-validate accounting logic ──────────────────
    validation_warnings = validate_facts_accounting_logic(llm_evidence_json.get("facts", []))
    if validation_warnings:
        llm_evidence_json["validation_warnings"] = validation_warnings

    evidence_json = {
        "question": question,
        "analysis_goal": plan.get("analysis_goal"),
        "company": company,
        "available_reports": available_reports,
        "retrieval_results": retrieval_results,
        "llm_evidence": llm_evidence_json,
    }

    fulfilled_items = 0
    planned_items = 0
    fulfilled_details = []
    planned_details = []
    planned_detail_keys = set()
    for item in retrieval_results:
        requirement = item.get("requirement", {})
        query_result_map = {
            query_result.get("field_query"): query_result
            for query_result in item.get("query_results", [])
        }
        for value_item in item["values"]:
            field_query = value_item.get("field_query")
            query_result = query_result_map.get(field_query, {})
            selected_candidate = query_result.get("selected_candidate", {})
            period = value_item.get("period") or {}
            planned_detail_key = (
                selected_candidate.get("statement_type"),
                selected_candidate.get("concept_name"),
                period.get("year"),
                period.get("quarter"),
            )
            if selected_candidate and planned_detail_key in planned_detail_keys:
                continue
            if selected_candidate:
                planned_detail_keys.add(planned_detail_key)
            detail = {
                "requirement": requirement,
                "field_query": field_query,
                "selected_candidate": selected_candidate,
                "period": period,
                "result": value_item.get("result"),
                "is_fulfilled": value_item.get("result") is not None,
            }
            planned_details.append(detail)
            planned_items += 1
            if value_item.get("result") is not None:
                fulfilled_items += 1
                fulfilled_details.append(detail)

    print_unique_log_item_names("[semantic_retrieval] fulfilled_items_list:", fulfilled_details)
    print_unique_log_item_names("[semantic_retrieval] planned_items_list:", planned_details)

    print("[semantic_retrieval] all_requirement_field_queries:")
    requirements = plan.get("requirements", [])
    if requirements:
        for index, requirement in enumerate(requirements, start=1):
            field_queries = requirement.get("field_query") or []
            if field_queries:
                for field_query in field_queries:
                    print(f"- requirement_{index}: {field_query}")
            else:
                print(f"- requirement_{index}: 無")
    else:
        print("- 無")

    print("fulfilled_items =", fulfilled_items)
    print("planned_items =", planned_items)
    enough_information = bool(llm_evidence_json.get("facts"))

    if not enough_information:
        print(f"[timing] semantic_retrieval.total took {perf_counter() - started_at:.3f}s")
        return {
            **state,
            "answer": "我已分析需要的財務資料並查詢資料庫，但目前資料不足以完整回答這個問題。",
            "reference_data": evidence_json,
        }

    # ── IMPROVED: final_prompt with all rules ─────────────────
    validation_note = ""
    if validation_warnings:
        validation_note = f"""
        ### 資料驗證警告
        以下欄位的數值可能有誤，請謹慎引用：
        {chr(10).join(f'- {w}' for w in validation_warnings)}
        """

    final_prompt = f"""
        你是信用徵審財報分析助手。系統資料來自台灣 IFRS 財務報表，查詢以繁體中文為主。
        請只根據 JSON evidence 回答，不要臆測。

        規則：
        1. 只能引用 facts 與 computed_metrics，不要引用 excluded_or_low_confidence_facts 作為判斷依據。
        2. 若需要比較、趨勢、增減或比率，優先使用 computed_metrics；不足時才用 facts 中的數值計算。
        3. 回答使用繁體中文，數值請加上千分位與單位。
        4. 若有被排除或低可信資料，只能在補充說明簡短提醒，不要拿來下結論。
        5. 絕對不可在回答中顯示任何 XBRL 代碼、concept_name 或技術欄位 ID。
           例如 ifrs-full:CashAndCashEquivalents、tifrs-bsci-ci_xxx 這類格式絕對不能出現在回答中。
        6. 用自然口語化的繁體中文回答，像一位專業財務助手在對話，避免顯示技術性內部資料或使用 backtick (`) 包住文字。
        7. 若 JSON 中有 validation_warnings，表示部分數值可能抓取錯誤，回答時對這些數值保持保留態度，不要用來下關鍵結論。
        8. computed_metrics 中的比率已由系統自動計算，數值可信度高於個別 facts，優先引用。

        請依照以下格式回答：
        一、關鍵證據
        - 列出本次回答實際引用的 3 到 8 筆關鍵數據、比率或事實。
        - 每一點盡量包含欄位名稱、期間、數值。

        二、分析結論
        - 根據上面的證據，直接回答使用者問題，並說明判斷依據。

        三、補充說明
        - 若有重要但未查到、被排除或低可信的欄位，簡短補充即可。

        ### 使用者問題
        {question}
        {validation_note}
        ### JSON 證據資料
        {json.dumps(llm_evidence_json, ensure_ascii=False, indent=2)}
        """

    try:
        print("[semantic_retrieval] final_answer prompt:\n" + final_prompt)
        step_started_at = perf_counter()
        final_answer = get_message_text(chat_model.invoke(final_prompt))
        print(f"[timing] semantic_retrieval.final_answer_generation took {perf_counter() - step_started_at:.3f}s")
    except Exception as exc:
        final_answer = (
            "已查到足夠的財務資料，但最終分析回答階段失敗。"
            f"你可以先參考 reference_data 中的 JSON 證據。錯誤：{exc}"
        )
    print("[semantic_retrieval] final_answer:\n" + str(final_answer))
    print(f"[timing] semantic_retrieval.total took {perf_counter() - started_at:.3f}s")

    return {
        **state,
        "answer": final_answer,
        "reference_data": evidence_json,
    }
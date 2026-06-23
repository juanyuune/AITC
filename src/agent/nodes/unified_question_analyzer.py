# ─────────────────────────────────────────────────────────────
# src/agent/nodes/unified_question_analyzer.py
# NEW FILE — replaces these four nodes in one single LLM call:
#   - rephrase_question.py
#   - classify_is_question_in_range.py  (was always True — removed)
#   - classify_question_type.py
#   - classify_statement_type.py
#
# BEFORE: 4–6 LLM calls just to understand the question
# AFTER:  1 structured LLM call, returns everything at once
# ─────────────────────────────────────────────────────────────

import os
from time import perf_counter
from typing import Literal, List

from pydantic import BaseModel, Field, field_validator

from src.providers.chat_openAI_provider import chat_model
from src.types.langgraph_state_types import OverallState


# ── Output schema ──────────────────────────────────────────────
# One structured LLM call returns all of these simultaneously.
# Previously these came from 4 separate node calls.

class UnifiedAnalysis(BaseModel):
    rephrased_question: str = Field(
        description=(
            "Rewrite the question to be fully self-contained. "
            "If it is already clear and specific (e.g. has company name, "
            "year, quarter, and field), return it unchanged."
        )
    )
    question_type: Literal["EXACT_QUERY", "SEMANTIC", "ANALYSIS", "DECISION"] = Field(
        description=(
            "EXACT_QUERY — user asks for a specific numeric value or field "
            "for a company and reporting period. "
            "SEMANTIC — asks about meaning or definition without judgment. "
            "ANALYSIS — asks for trend analysis, risk review, or reasoning "
            "across multiple data points. "
            "DECISION — asks for a recommendation or go/no-go judgment."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the question_type classification (0.0 to 1.0).",
    )
    statement_types: List[Literal[
        "balance_sheet",
        "comprehensive_income_statement",
        "statement_of_cash_flows",
    ]] = Field(
        description=(
            "All financial statement types needed to answer. "
            "balance_sheet: assets, liabilities, equity, cash. "
            "comprehensive_income_statement: revenue, costs, profit, expenses. "
            "statement_of_cash_flows: operating/investing/financing cash flows."
        )
    )
    primary_statement_type: Literal[
        "balance_sheet",
        "comprehensive_income_statement",
        "statement_of_cash_flows",
    ] = Field(
        description="The single most important statement to query first."
    )

    @field_validator("statement_types", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v


# ── Node function ──────────────────────────────────────────────

def unified_question_analyzer(state: OverallState) -> OverallState:
    """
    Replaces four sequential nodes with one structured LLM call.

    BEFORE (4 nodes, 4–6 calls):
        rephrase_question
        → classify_is_question_in_range  (always True — wasted call)
        → classify_question_type
        → classify_statement_type

    AFTER (1 node, 1 call):
        unified_question_analyzer

    Falls back to safe defaults if structured output fails,
    so the graph always continues running.
    """
    started_at = perf_counter()
    question = state.get("user_input", "").strip()

    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import PromptTemplate
    from langchain_core.runnables import RunnableLambda
    import re as _re

    parser = JsonOutputParser(pydantic_object=UnifiedAnalysis)

    def extract_and_normalize(message):
        text = message.content if hasattr(message, "content") else str(message)
        text = _re.sub(r"```[a-z]*\s*", "", text)
        text = _re.sub(r"```\s*", "", text)
        text = text.strip()
        start = text.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{": depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:i+1]
        if "question_type" in text or "rephrased" in text:
            return "{" + text + "}"
        return text

    prompt_template = PromptTemplate(
        template="""你是台灣信用徵信 AI 系統的財務報表分析器。
只輸出 JSON，不要任何說明文字或 markdown。

{format_instructions}

使用者問題：{question}

任務一 — rephrased_question：若問題已完整直接回傳原文，否則改寫為完整問題。

任務二 — question_type：
EXACT_QUERY → 詢問特定數值或欄位（例：現金及約當現金、應收帳款淨額、營業收入）
ANALYSIS → 趨勢分析、風險評估、跨期比較
SEMANTIC → 詢問定義說明
DECISION → 詢問建議結論

任務三 — statement_types（可多選）：
balance_sheet → 資產、負債、權益、現金及約當現金、應收帳款、存貨
comprehensive_income_statement → 營收、成本、利潤、費用、毛利率、EPS
statement_of_cash_flows → 營業/投資/籌資活動現金流

任務四 — confidence：0.0到1.0的信心分數
任務五 — primary_statement_type：最重要的一張報表""",
        input_variables=["question"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )

    chain = prompt_template | chat_model | RunnableLambda(extract_and_normalize) | parser

    # ── Keyword-based statement type override ─────────────────
    # Breeze2 is unreliable at classifying statement types.
    # Use hardcoded rules for common terms — fast and accurate.
    BALANCE_SHEET_KEYWORDS = [
        "現金及約當現金", "現金水位", "應收帳款", "應收票據", "存貨", "預付款項",
        "總資產", "資產總額", "資產總計", "資產合計",
        "負債總額", "負債總計", "負債合計", "負債及權益總計",
        "權益總額", "權益總計", "股東權益", "保留盈餘",
        "流動資產", "流動負債", "流動比率",
        "非流動資產", "非流動負債",
        "應付帳款", "應付票據", "短期借款", "長期借款", "長期負債",
        "不動產廠房及設備", "無形資產", "遞延所得稅",
        "每股淨值", "淨值",
    ]
    INCOME_KEYWORDS = [
        "營業收入", "營收", "毛利", "毛利率", "營業費用", "營業利益",
        "稅前淨利", "稅後淨利", "淨利", "每股盈餘", "EPS", "營業成本",
        "利息收入", "其他收入", "綜合損益",
    ]
    CASHFLOW_KEYWORDS = [
        "營業活動現金流", "投資活動現金流", "籌資活動現金流",
        "自由現金流", "資本支出", "折舊", "攤銷",
    ]

    def detect_statement_types(q):
        found = []
        if any(kw in q for kw in BALANCE_SHEET_KEYWORDS):
            found.append("balance_sheet")
        if any(kw in q for kw in INCOME_KEYWORDS):
            found.append("comprehensive_income_statement")
        if any(kw in q for kw in CASHFLOW_KEYWORDS):
            found.append("statement_of_cash_flows")
        return found or None

    keyword_types = detect_statement_types(question)

    # If question is clearly analytical, ensure both statement types included
    ANALYSIS_KEYWORDS = ["是否充足", "是否有風險", "獲利能力", "負債結構", "趨勢",
                         "是否持續", "分析", "評估", "水位"]
    if any(kw in question for kw in ANALYSIS_KEYWORDS):
        if not keyword_types:
            keyword_types = ["balance_sheet", "comprehensive_income_statement"]
        elif "balance_sheet" not in keyword_types:
            keyword_types.append("balance_sheet")
        elif "comprehensive_income_statement" not in keyword_types:
            keyword_types.append("comprehensive_income_statement")

    try:
        result = chain.invoke({"question": question})
        if hasattr(result, "model_dump"):
            result = result.model_dump()
    except Exception as exc:
        print(f"[unified_question_analyzer] structured output failed: {exc}")
        print("[unified_question_analyzer] using safe defaults")
        result = {
            "rephrased_question": question,
            "question_type": "EXACT_QUERY",
            "confidence": 0.5,
            "statement_types": ["balance_sheet"],
            "primary_statement_type": "balance_sheet",
        }

    # Override statement_types with keyword detection if available
    if keyword_types:
        result["statement_types"] = keyword_types
        result["primary_statement_type"] = keyword_types[0]
        print(f"[unified_question_analyzer] keyword_override statement_types={keyword_types}")

    # Guard against empty rephrased question
    if not result.get("rephrased_question", "").strip():
        result["rephrased_question"] = question

    elapsed = perf_counter() - started_at
    print(f"[timing] unified_question_analyzer took {elapsed:.3f}s")
    print(f"[unified_question_analyzer] question_type={result['question_type']} "
          f"confidence={result['confidence']:.2f}")
    print(f"[unified_question_analyzer] statement_types={result['statement_types']}")
    print(f"[unified_question_analyzer] rephrased={result['rephrased_question']}")

    # Build statement_type_result in the same shape downstream nodes expect
    statement_type_result = {
        "statement_types": result["statement_types"],
        "primary_statement_type": result["primary_statement_type"],
    }

    return {
        **state,
        # Output matching rephrase_question
        "rephrased_question": result["rephrased_question"],
        # Output matching classify_question_type
        "question_type": result["question_type"],
        "question_type_confidence": result["confidence"],
        "question_type_result": result,
        # Output matching classify_statement_type
        "statement_type": result["primary_statement_type"],
        "statement_types": result["statement_types"],
        "statement_type_result": statement_type_result,
        # classify_is_question_in_range always returned "True" — keep field
        # in state for compatibility but no LLM call is made anymore
        "is_question_in_range": "True",
    }
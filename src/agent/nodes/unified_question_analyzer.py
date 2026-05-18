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

    structured_llm = chat_model.with_structured_output(UnifiedAnalysis)

    prompt = f"""你是台灣信用徵信 AI 系統的財務報表分析器。

請分析以下使用者問題，一次性回傳完整結構化分析。

任務一 — 改寫問題：
若問題已完整（含公司名稱、年份、季度、欄位），直接原文回傳。
若問題不完整或依賴前文，改寫為可獨立理解的完整問題。

任務二 — 分類問題類型：
EXACT_QUERY  → 詢問特定公司某期間的特定數值或欄位
              例：請給我台泥 2024年Q1 的現金及約當現金
SEMANTIC     → 詢問定義、說明或廣泛查詢，不需判斷
ANALYSIS     → 詢問趨勢分析、風險評估或跨多筆資料的推理
              例：台泥 2024年的現金水位是否充足？
DECISION     → 詢問建議、結論或評估

任務三 — 識別所需財務報表：
balance_sheet                   → 資產、負債、權益、現金及約當現金
comprehensive_income_statement  → 營收、成本、利潤、費用、毛利率
statement_of_cash_flows         → 營業/投資/籌資活動現金流

若需要多張報表請全部列出。

使用者問題：{question}
"""

    try:
        result = structured_llm.invoke(prompt).model_dump()
    except Exception as exc:
        # Safe fallback — graph continues with conservative defaults
        print(f"[unified_question_analyzer] structured output failed: {exc}")
        print("[unified_question_analyzer] using safe defaults")
        result = {
            "rephrased_question": question,
            "question_type": "EXACT_QUERY",
            "confidence": 0.0,
            "statement_types": ["balance_sheet"],
            "primary_statement_type": "balance_sheet",
        }

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
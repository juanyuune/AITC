from typing_extensions import TypedDict, NotRequired
from typing import Any
from langgraph.graph import MessagesState


class OverallState(TypedDict):
    messages: MessagesState
    is_question_in_range: str
    user_input: str
    rephrased_question: str
    question_type: str
    question_type_confidence: float
    question_type_result: dict[str, Any]
    statement_type: str
    statement_types: list[str]
    statement_type_result: dict[str, Any]
    reference_data: Any
    answer: str
    # ── Inference Dispatcher ──────────────────────────────────
    retrieved_sources: list[str]      # set by exact_query / semantic_retrieval
    dispatch_decision: str            # set by dispatch_node: "private" or "cloud"

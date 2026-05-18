# ─────────────────────────────────────────────────────────────
# src/agent/graph.py
# CHANGED: Phase 1 + Phase 2 applied
#   - classify_is_question_in_range REMOVED (always returned True — dead node)
#   - rephrase_question REMOVED (merged into unified_question_analyzer)
#   - classify_question_type REMOVED (merged into unified_question_analyzer)
#   - classify_statement_type REMOVED (merged into unified_question_analyzer)
#   - unified_question_analyzer ADDED (replaces all four above in 1 LLM call)
# ─────────────────────────────────────────────────────────────

# import LangGraph lib
from langgraph.graph import StateGraph, START, END
from langgraph.graph import MessagesState
from typing_extensions import TypedDict, NotRequired, Annotated

# import langGraph nodes
# REMOVED: rephrase_question, classify_is_question_in_range,
#          classify_question_type, classify_statement_type
from src.agent.nodes.unified_question_analyzer import unified_question_analyzer
from src.agent.nodes.exact_query import exact_query
from src.agent.nodes.semantic_retrieval import semantic_retrieval
from src.agent.nodes.question_out_of_range import question_out_of_range

# import type
from src.types.langgraph_state_types import OverallState


# Route from unified_question_analyzer to the correct retrieval path
def question_type_condition_edge(state: OverallState) -> str:
    match state["question_type"]:
        case "EXACT_QUERY":
            # unified_question_analyzer already set statement_type,
            # so we go straight to exact_query (no classify_statement_type needed)
            return "exact_query"
        case _:
            return "semantic_retrieval"


# 宣告Graph Workflow
workflow = StateGraph(OverallState)

# 宣告LangGraph Node
# REMOVED: rephrase_question, classify_is_question_in_range,
#          classify_question_type, classify_statement_type
workflow.add_node(unified_question_analyzer)
workflow.add_node(exact_query)
workflow.add_node(semantic_retrieval)
workflow.add_node(question_out_of_range)

# 宣告LangGraph Edge
# START → unified_question_analyzer (replaces 4 old nodes)
workflow.add_edge(START, "unified_question_analyzer")

# Route directly from unified analyzer to retrieval
workflow.add_conditional_edges(
    source="unified_question_analyzer",
    path=question_type_condition_edge,
    path_map={
        "exact_query": "exact_query",
        "semantic_retrieval": "semantic_retrieval",
    },
)

workflow.add_edge("exact_query", END)
workflow.add_edge("semantic_retrieval", END)

# question_out_of_range is kept as a safety node but
# unified_question_analyzer always returns is_question_in_range="True"
# so this path is never triggered in normal operation
workflow.add_edge("question_out_of_range", END)

graph = workflow.compile()
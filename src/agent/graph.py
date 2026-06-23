# src/agent/graph.py
# Updated: Inference Dispatcher added between retrieval and answer generation.
# exact_query and semantic_retrieval both route through dispatch_node.
# dispatch_node decides: generate_answer_onpremise or generate_answer_cloud.

from langgraph.graph import StateGraph, START, END
from langgraph.graph import MessagesState
from typing_extensions import TypedDict, NotRequired, Annotated

# Retrieval and analysis nodes
from src.agent.nodes.unified_question_analyzer import unified_question_analyzer
from src.agent.nodes.exact_query import exact_query
from src.agent.nodes.semantic_retrieval import semantic_retrieval
from src.agent.nodes.question_out_of_range import question_out_of_range

# Dispatch nodes
from src.agent.nodes.dispatch_node import dispatch_node, get_dispatch_route
from src.agent.nodes.generate_answer_onpremise import generate_answer_onpremise
from src.agent.nodes.generate_answer_cloud import generate_answer_cloud

from src.types.langgraph_state_types import OverallState


def question_type_condition_edge(state: OverallState) -> str:
    match state["question_type"]:
        case "EXACT_QUERY":
            return "exact_query"
        case _:
            return "semantic_retrieval"


workflow = StateGraph(OverallState)

# ── Existing nodes ────────────────────────────────────────────
workflow.add_node(unified_question_analyzer)
workflow.add_node(exact_query)
workflow.add_node(semantic_retrieval)
workflow.add_node(question_out_of_range)

# ── New dispatch nodes ────────────────────────────────────────
workflow.add_node(dispatch_node)
workflow.add_node(generate_answer_onpremise)
workflow.add_node(generate_answer_cloud)

# ── Edges ─────────────────────────────────────────────────────
workflow.add_edge(START, "unified_question_analyzer")

workflow.add_conditional_edges(
    source="unified_question_analyzer",
    path=question_type_condition_edge,
    path_map={
        "exact_query": "exact_query",
        "semantic_retrieval": "semantic_retrieval",
    },
)

# Both retrieval nodes → dispatch_node (replaces direct → END)
workflow.add_edge("exact_query", "dispatch_node")
workflow.add_edge("semantic_retrieval", "dispatch_node")

# dispatch_node → on-premise or cloud answer generation
workflow.add_conditional_edges(
    source="dispatch_node",
    path=get_dispatch_route,
    path_map={
        "generate_answer_onpremise": "generate_answer_onpremise",
        "generate_answer_cloud": "generate_answer_cloud",
    },
)

workflow.add_edge("generate_answer_onpremise", END)
workflow.add_edge("generate_answer_cloud", END)
workflow.add_edge("question_out_of_range", END)

graph = workflow.compile()

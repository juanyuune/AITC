# src/agent/nodes/dispatch_node.py
# LangGraph node that wraps the Inference Dispatcher.
# Sits between retrieval nodes and answer generation.
# Returns dispatch_decision in state for conditional edge.

from src.types.langgraph_state_types import OverallState
from src.services.inference_dispatcher import dispatch
from src.services.data_sanitizer import sanitize_evidence_list


def dispatch_node(state: OverallState) -> OverallState:
    """
    Reads retrieved_sources from state.
    Sets dispatch_decision: 'private' or 'cloud'.
    Falls back to 'private' if sanitization fails.
    """
    retrieved_sources = state.get("retrieved_sources", [])
    decision = dispatch(retrieved_sources)

    # If cloud selected, verify evidence can be sanitized
    if decision == "cloud":
        reference_data = state.get("reference_data") or {}
        facts = reference_data.get("facts", [])
        evidence = facts if isinstance(facts, list) else [facts]
        try:
            if evidence:
                sanitize_evidence_list(evidence)
        except ValueError:
            print("[dispatch_node] sanitization failed → falling back to on-premise")
            decision = "private"

    print(f"[dispatch_node] decision={decision} sources={retrieved_sources}")
    return {**state, "dispatch_decision": decision}


def get_dispatch_route(state: OverallState) -> str:
    """
    Conditional edge function for graph.py.
    Returns the next node name.
    """
    decision = state.get("dispatch_decision", "private")
    if decision == "cloud":
        return "generate_answer_cloud"
    return "generate_answer_onpremise"

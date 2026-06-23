# src/agent/nodes/generate_answer_cloud.py
#
# ── What this node does ───────────────────────────────────────
# Handles queries where ALL retrieved sources are classified as
# PUBLIC in data_classifier_config.py (e.g. published_xbrl_reports,
# web_search, public_benchmarks).
#
# ── Current status: STUB ──────────────────────────────────────
# The answer is already generated upstream by exact_query.py or
# semantic_retrieval.py. This node currently just tags the dispatch
# path as "cloud" and passes the state through unchanged.
#
# ── Why it exists as a stub ───────────────────────────────────
# In practice, ALL current queries hit FinancialStatementXBRL.db,
# which is in PRIVATE_SOURCES. The cloud path never fires in
# normal usage. This node is a placeholder for a future feature.
#
# ── How to wire it up when ready ─────────────────────────────
# Step 1: Import a cloud-capable chat model (e.g. ChatAnthropic
#         or ChatOpenAI pointing at Anthropic API, not vLLM):
#
#   from langchain_anthropic import ChatAnthropic
#   cloud_model = ChatAnthropic(model="claude-sonnet-4-6")
#
# Step 2: Replace the pass-through with an actual LLM call:
#
#   prompt = f"""
#   Question: {state['user_input']}
#   Retrieved context: {state.get('reference_data', '')}
#   Answer in Traditional Chinese.
#   """
#   response = cloud_model.invoke(prompt)
#   answer = response.content
#
# Step 3: Add data sanitization before the cloud call:
#   from src.services.data_sanitizer import sanitize_for_cloud
#   safe_context = sanitize_for_cloud(state.get('reference_data', ''))
#   This strips PRIVATE_FIELDS (client_id, loan_amount, etc.)
#   defined in data_classifier_config.py before sending to cloud.
#
# Step 4: Update dispatch_node.py to route more query types
#         to this path based on data_classifier_config.PUBLIC_SOURCES.
#
# ── Security note ─────────────────────────────────────────────
# Never send data from PRIVATE_SOURCES to this node.
# dispatch_node.py enforces this — do not bypass it.
# ─────────────────────────────────────────────────────────────

from src.types.langgraph_state_types import OverallState


def generate_answer_cloud(state: OverallState) -> OverallState:
    """
    Cloud answer generation path — currently a pass-through stub.

    Fires when dispatch_node determines all retrieved sources are
    PUBLIC (see data_classifier_config.py). In practice this never
    fires today because FinancialStatementXBRL.db is PRIVATE.

    TODO: Replace with actual cloud model call when cloud routing
    is ready. See implementation notes at the top of this file.
    """
    print("[generate_answer_cloud] answer ready — dispatched via cloud path (stub)")
    return {
        **state,
        "dispatch_decision": "cloud",
    }

"""
Wires every node in agent/nodes/ into the StateGraph described in the plan
(see the "LangGraph graph design" diagram). Kept deliberately declarative --
all the actual logic lives in the node functions, this file is just edges.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from agent import config
from agent.nodes.apply_fix import apply_fix
from agent.nodes.classify_error import classify_error
from agent.nodes.escalate import escalate
from agent.nodes.fetch_logs import fetch_logs
from agent.nodes.introspect_schema import introspect_schema
from agent.nodes.notify_success import notify_success
from agent.nodes.open_pr_and_label import open_pr_and_label
from agent.nodes.open_pr_and_merge import open_pr_and_merge
from agent.nodes.propose_fix import propose_fix
from agent.nodes.retrigger_dag import retrigger_dag
from agent.nodes.revert_and_escalate import revert_and_escalate
from agent.nodes.risk_gate import risk_gate
from agent.nodes.validate_fix import validate_fix
from agent.state import SelfHealState

_FIXABLE_ERROR_TYPES = {"missing_column", "compile_error"}


def route_classification(state: SelfHealState) -> str:
    return "fixable" if state.get("error_type") in _FIXABLE_ERROR_TYPES else "not_fixable"


def route_validation(state: SelfHealState) -> str:
    if state.get("validation_passed"):
        return "pass"
    if state.get("retry_count", 0) < config.MAX_FIX_RETRIES:
        return "retry"
    return "escalate"


def route_risk(state: SelfHealState) -> str:
    return state.get("risk_level", "needs_review")


def route_retrigger(state: SelfHealState) -> str:
    return "success" if state.get("retrigger_succeeded") else "fail"


def build_graph():
    graph = StateGraph(SelfHealState)

    graph.add_node("fetch_logs", fetch_logs)
    graph.add_node("classify_error", classify_error)
    graph.add_node("introspect_schema", introspect_schema)
    graph.add_node("propose_fix", propose_fix)
    graph.add_node("apply_fix", apply_fix)
    graph.add_node("validate_fix", validate_fix)
    graph.add_node("risk_gate", risk_gate)
    graph.add_node("open_pr_and_merge", open_pr_and_merge)
    graph.add_node("open_pr_and_label", open_pr_and_label)
    graph.add_node("retrigger_dag", retrigger_dag)
    graph.add_node("notify_success", notify_success)
    graph.add_node("escalate", escalate)
    graph.add_node("revert_and_escalate", revert_and_escalate)

    graph.set_entry_point("fetch_logs")
    graph.add_edge("fetch_logs", "classify_error")
    graph.add_conditional_edges(
        "classify_error", route_classification, {"fixable": "introspect_schema", "not_fixable": "escalate"}
    )
    graph.add_edge("introspect_schema", "propose_fix")
    graph.add_edge("propose_fix", "apply_fix")
    graph.add_edge("apply_fix", "validate_fix")
    graph.add_conditional_edges(
        "validate_fix", route_validation, {"retry": "propose_fix", "escalate": "escalate", "pass": "risk_gate"}
    )
    graph.add_conditional_edges(
        "risk_gate", route_risk, {"low_risk": "open_pr_and_merge", "needs_review": "open_pr_and_label"}
    )
    graph.add_edge("open_pr_and_merge", "retrigger_dag")
    graph.add_conditional_edges(
        "retrigger_dag", route_retrigger, {"success": "notify_success", "fail": "revert_and_escalate"}
    )
    graph.add_edge("notify_success", END)
    graph.add_edge("open_pr_and_label", END)
    graph.add_edge("escalate", END)
    graph.add_edge("revert_and_escalate", END)

    return graph.compile()

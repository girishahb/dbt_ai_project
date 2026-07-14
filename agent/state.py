"""State passed between every node in the graph (agent/graph.py).

A plain TypedDict, not a pydantic model -- LangGraph merges partial dict
returns from each node into this shape automatically, and every field here
is JSON-serializable so the whole state can be logged for an audit trail
without extra work.
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict


class SelfHealState(TypedDict, total=False):
    # --- failure identity (set once, at graph start) ---
    dag_id: str
    task_id: str
    run_id: str
    try_number: int
    log_url: str

    # --- fetch_logs ---
    log_text: str

    # --- classify_error ---
    error_type: Literal["missing_column", "compile_error", "data_quality", "unknown"]
    affected_model: str
    affected_column: Optional[str]
    classification_reasoning: str

    # --- introspect_schema ---
    schema_context: str  # DESCRIBE TABLE output for the relevant source/upstream model(s)

    # --- propose_fix / apply_fix ---
    repo_path: str
    branch_name: str
    proposed_files: dict[str, str]  # relative path -> full new file content
    fix_reasoning: str
    retry_count: int
    previous_attempts: list[str]  # short summaries of failed attempts, fed back into the next proposal

    # --- validate_fix ---
    validation_output: str
    validation_passed: bool

    # --- risk_gate ---
    risk_level: Literal["low_risk", "needs_review"]
    risk_reasons: list[str]

    # --- open_pr_and_merge / open_pr_and_label ---
    pr_number: int
    pr_url: str
    merged: bool

    # --- retrigger_dag ---
    retrigger_run_id: str
    retrigger_succeeded: bool

    # --- terminal ---
    final_status: Literal[
        "fixed_and_verified",
        "pr_open_needs_review",
        "escalated_unfixable",
        "escalated_validation_exhausted",
        "escalated_reverted_after_remerge_failure",
    ]
    final_message: str

from agent import config
from agent.state import SelfHealState
from agent.tools import repo_tools


def risk_gate(state: SelfHealState) -> dict:
    """Every check here is independent -- any single failing check is
    enough to route to needs_review, since the point is a *narrow* allow-
    list for auto-merge, not a majority vote."""
    files_changed, total_line_changes = repo_tools.diff_stat(state["repo_path"])
    changed = repo_tools.changed_files(state["repo_path"])
    reasons: list[str] = []

    if files_changed > config.LOW_RISK_MAX_FILES:
        reasons.append(f"{files_changed} files changed (allow-list max {config.LOW_RISK_MAX_FILES})")
    if total_line_changes > config.LOW_RISK_MAX_LINES:
        reasons.append(f"{total_line_changes} lines changed (allow-list max {config.LOW_RISK_MAX_LINES})")
    if state["error_type"] not in config.LOW_RISK_ERROR_TYPES:
        reasons.append(f"error_type '{state['error_type']}' is not in the low-risk allow-list {config.LOW_RISK_ERROR_TYPES}")

    disallowed = [f for f in changed if not f.startswith(config.ALLOWED_WRITE_PREFIXES)]
    if disallowed:
        reasons.append(f"touched disallowed paths: {disallowed}")

    if not reasons:
        return {
            "risk_level": "low_risk",
            "risk_reasons": [f"{files_changed} file(s) / {total_line_changes} line(s) changed, within allow-list thresholds"],
        }
    return {"risk_level": "needs_review", "risk_reasons": reasons}

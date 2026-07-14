from agent.state import SelfHealState
from agent.tools import repo_tools


def apply_fix(state: SelfHealState) -> dict:
    """Commits locally only -- no push yet. dbt doesn't care about git
    history, only what's on disk, so validate_fix runs fine against an
    uncommitted push; we only need a real commit+push once we're ready to
    open a PR (see nodes/_pr_common.py), which keeps the remote branch free
    of intermediate failed-attempt commits."""
    message = f"self-heal: fix {state['affected_model']} ({state['error_type']})\n\n{state.get('fix_reasoning', '')}"
    repo_tools.commit(state["repo_path"], message)
    return {}

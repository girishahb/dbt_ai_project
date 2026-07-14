"""
Entrypoint for the agent's container (see agent/Dockerfile). Reads the
failure context the dispatcher Lambda passed in as container environment
overrides (agent/config.py), runs the graph once end-to-end, and exits --
this is a run-to-completion Fargate task, not a long-lived service.
"""
from __future__ import annotations

import json
import sys

from agent import config
from agent.graph import build_graph


def main() -> int:
    if not config.FAILURE_DAG_ID:
        print("FAILURE_DAG_ID not set -- nothing to do (see agent/README.md for local testing).")
        return 1

    initial_state = {
        "dag_id": config.FAILURE_DAG_ID,
        "task_id": config.FAILURE_TASK_ID,
        "run_id": config.FAILURE_RUN_ID,
        "try_number": config.FAILURE_TRY_NUMBER,
        "log_url": config.FAILURE_LOG_URL,
    }

    app = build_graph()
    final_state = app.invoke(initial_state, config={"recursion_limit": 50})

    print("=== self-heal agent run complete ===")
    print(json.dumps({k: v for k, v in final_state.items() if k != "log_text"}, indent=2, default=str))

    return 0 if final_state.get("final_status") in ("fixed_and_verified", "pr_open_needs_review") else 1


if __name__ == "__main__":
    sys.exit(main())

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from agent.state import SelfHealState
from agent.tools import repo_tools
from agent.tools.bedrock import get_model

_MAX_TOOL_ITERATIONS = 8


def propose_fix(state: SelfHealState) -> dict:
    repo_path = state["repo_path"]
    branch_name = state["branch_name"]

    if repo_tools.branch_exists_locally(repo_path, branch_name):
        repo_tools.checkout(repo_path, branch_name)
    else:
        repo_tools.create_branch(repo_path, branch_name)

    # Closures over repo_path so the LLM only ever deals in project-relative
    # paths, never absolute filesystem paths (keeps prompts smaller and
    # means the model can't accidentally construct a path that escapes the
    # clone -- repo_tools.write_file double-checks that anyway).
    @tool
    def read_file(path: str) -> str:
        """Read a file's contents. `path` is relative to the dbt project root, e.g. 'models/silver/silver_customers.sql'."""
        return repo_tools.read_file(repo_path, path)

    @tool
    def list_dir(path: str = ".") -> str:
        """List files in a directory. `path` is relative to the dbt project root."""
        return repo_tools.list_dir(repo_path, path)

    @tool
    def write_file(path: str, content: str) -> str:
        """Overwrite a file with new FULL file content (not a diff/patch). Only paths under models/ are permitted -- anything else is rejected."""
        try:
            return repo_tools.write_file(repo_path, path, content)
        except PermissionError as exc:
            return f"ERROR: {exc}"

    tools_by_name = {"read_file": read_file, "list_dir": list_dir, "write_file": write_file}
    llm = get_model(temperature=0.2).bind_tools(list(tools_by_name.values()))

    previous_attempts_text = ""
    if state.get("previous_attempts"):
        previous_attempts_text = "\n\nPrevious attempts that did NOT fix the issue -- do not repeat them:\n" + "\n".join(
            f"- {a}" for a in state["previous_attempts"]
        )

    messages = [
        SystemMessage(
            content=(
                "You are fixing a broken dbt model in a Databricks medallion (bronze/silver/gold) project. "
                f"The failing model is `{state['affected_model']}` "
                f"(column involved: {state.get('affected_column') or 'unknown'}).\n\n"
                "Use read_file/list_dir to inspect the current model and any related files, then call "
                "write_file exactly once per file you need to change, passing the FULL corrected file "
                "content. You may ONLY modify files under models/ -- write_file refuses anything else "
                "by construction, so don't try tests/, macros/, dbt_project.yml, or profiles/. "
                "Make the smallest change that actually fixes the root cause -- don't refactor unrelated "
                "code, don't loosen or remove tests. When you're done, reply with a short plain-text "
                "summary of what you changed and why, and stop calling tools."
            )
        ),
        HumanMessage(
            content=(
                f"Airflow task log (tail):\n{state['log_text']}\n\n"
                f"Classification: {state['error_type']} -- {state.get('classification_reasoning', '')}\n\n"
                f"Actual Databricks schema of upstream/related tables:\n{state.get('schema_context', '')}"
                + previous_attempts_text
            )
        ),
    ]

    written_files: list[str] = []
    fix_reasoning = "(model did not provide a summary)"
    for _ in range(_MAX_TOOL_ITERATIONS):
        response = llm.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            fix_reasoning = response.content
            break
        for tool_call in response.tool_calls:
            result = tools_by_name[tool_call["name"]].invoke(tool_call["args"])
            if tool_call["name"] == "write_file":
                written_files.append(tool_call["args"]["path"])
            messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))
    else:
        fix_reasoning = "(tool-calling loop hit its iteration limit without a final summary)"

    return {
        "proposed_files": {path: repo_tools.read_file(repo_path, path) for path in written_files},
        "fix_reasoning": fix_reasoning,
    }

import os
import re

from agent.state import SelfHealState
from agent.tools.databricks_introspect import describe_table

_SOURCE_RE = re.compile(r"source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)")
_REF_RE = re.compile(r"ref\(\s*['\"](\w+)['\"]\s*\)")

_CATALOG = os.environ.get("DBT_DATABRICKS_CATALOG", "ai_project")


def _find_model_file(repo_path: str, model_name: str) -> str | None:
    for layer in ("silver", "gold"):
        candidate = os.path.join(repo_path, "models", layer, f"{model_name}.sql")
        if os.path.isfile(candidate):
            return candidate
    return None


def introspect_schema(state: SelfHealState) -> dict:
    repo_path = state["repo_path"]
    model_path = _find_model_file(repo_path, state["affected_model"])
    if not model_path:
        return {"schema_context": f"(could not find models/{{silver,gold}}/{state['affected_model']}.sql)"}

    with open(model_path, encoding="utf-8") as fh:
        sql_text = fh.read()

    sections = []
    for _source_name, table in _SOURCE_RE.findall(sql_text):
        sections.append(f"-- source table {table} (bronze) --\n" + describe_table(_CATALOG, "default", table))
    for ref_model in _REF_RE.findall(sql_text):
        layer = "silver" if os.path.isfile(os.path.join(repo_path, "models", "silver", f"{ref_model}.sql")) else "gold"
        sections.append(f"-- ref'd model {ref_model} ({layer}) --\n" + describe_table(_CATALOG, layer, ref_model))

    # Also describe the affected model's own current (deployed) shape, if it
    # exists yet -- useful when the bug is a rename rather than a brand new
    # column that was never there.
    layer = "silver" if os.path.dirname(model_path).endswith("silver") else "gold"
    sections.append(
        f"-- current deployed shape of {state['affected_model']} ({layer}) --\n"
        + describe_table(_CATALOG, layer, state["affected_model"])
    )

    return {"schema_context": "\n\n".join(sections)}

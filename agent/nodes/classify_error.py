import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from agent import config
from agent.state import SelfHealState
from agent.tools import github_client, repo_tools
from agent.tools.bedrock import get_model


class ErrorClassification(BaseModel):
    error_type: str = Field(
        description="One of: missing_column, compile_error, data_quality, unknown. "
        "data_quality means the failure is a test catching genuinely bad/unexpected "
        "*data* (not a bug in the SQL) -- never classify a not_null/relationships/"
        "accepted_values failure caused by real source data as missing_column or "
        "compile_error just because it looks fixable."
    )
    affected_model: str = Field(description="The dbt model name most directly responsible, e.g. 'silver_customers'.")
    affected_column: str = Field(default="", description="The specific column name involved, if any, else empty string.")
    reasoning: str = Field(description="One or two sentences on how you reached this conclusion from the log text.")


def classify_error(state: SelfHealState) -> dict:
    repo_path = os.path.join(config.WORKDIR, state["run_id"])
    repo_tools.clone_repo(github_client.clone_url_with_token(), repo_path)

    model_files = "\n".join(
        f"models/silver/{f}" for f in os.listdir(os.path.join(repo_path, "models", "silver")) if f.endswith(".sql")
    ) + "\n" + "\n".join(
        f"models/gold/{f}" for f in os.listdir(os.path.join(repo_path, "models", "gold")) if f.endswith(".sql")
    )

    parser = PydanticOutputParser(pydantic_object=ErrorClassification)
    result: ErrorClassification = parser.parse(
        get_model().invoke(
            [
                SystemMessage(
                    content=(
                        "You are diagnosing a failed Airflow task that ran `dbt run`/`dbt test` "
                        "for a Databricks medallion (silver/gold) dbt project. Read the task log "
                        "and classify the failure. Existing model files in this project:\n"
                        + model_files
                        + "\n\n"
                        + parser.get_format_instructions()
                    )
                ),
                HumanMessage(content=f"Airflow task log (tail):\n\n{state['log_text']}"),
            ]
        ).content
    )

    return {
        "repo_path": repo_path,
        "branch_name": f"auto-fix/{state['dag_id']}-{state['run_id']}".replace(":", "-").replace("+", "-"),
        "error_type": result.error_type,
        "affected_model": result.affected_model,
        "affected_column": result.affected_column or None,
        "classification_reasoning": result.reasoning,
        "retry_count": 0,
        "previous_attempts": [],
    }

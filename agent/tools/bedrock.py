"""Single place that constructs the Bedrock chat model, so every node uses
the same model id/region/temperature instead of each hardcoding it."""
from __future__ import annotations

from langchain_aws import ChatBedrock

from agent import config


def get_model(temperature: float = 0.0):
    return ChatBedrock(
        model_id=config.BEDROCK_MODEL_ID,
        region_name=config.BEDROCK_REGION,
        model_kwargs={"temperature": temperature},
    )

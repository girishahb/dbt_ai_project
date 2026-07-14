# Run-to-completion container for the self-heal agent -- started once per
# DAG failure by the dispatcher Lambda's ECS RunTask call (infra/lambda.tf),
# exits when the graph reaches END. Not a long-lived service, so no health
# check / port exposed.
FROM python:3.11-slim

# git: needed to clone the dbt repo and create/push branches (tools/repo_tools.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY agent/requirements.txt ./agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt

COPY agent ./agent

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "agent.main"]

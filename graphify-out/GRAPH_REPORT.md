# Graph Report - dbt_ai_project  (2026-07-16)

## Corpus Check
- 65 files · ~50,586 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 261 nodes · 448 edges · 18 communities (17 shown, 1 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 24 edges (avg confidence: 0.71)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f8134d6c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- SelfHealState
- Setup: building this from zero
- dbt_ai_project
- github_client.py
- Troubleshooting a failed deploy-agent.yml run
- repo_tools.py
- Concepts
- dbt_common.py
- Architecture
- MwaaClient
- introspect_schema.py
- validate_fix.py
- handler.py
- startup.sh

## God Nodes (most connected - your core abstractions)
1. `SelfHealState` - 37 edges
2. `build_graph()` - 21 edges
3. `Concepts` - 18 edges
4. `revert_and_escalate()` - 16 edges
5. `Setup: building this from zero` - 14 edges
6. `MwaaClient` - 12 edges
7. `propose_fix()` - 11 edges
8. `Troubleshooting a failed deploy-agent.yml run` - 11 edges
9. `open_pr_and_merge()` - 10 edges
10. `Operations runbook` - 10 edges

## Surprising Connections (you probably didn't know these)
- `build_graph()` --indirect_call--> `introspect_schema()`  [INFERRED]
  agent/graph.py → agent/nodes/introspect_schema.py
- `build_graph()` --indirect_call--> `open_pr_and_merge()`  [INFERRED]
  agent/graph.py → agent/nodes/open_pr_and_merge.py
- `build_graph()` --indirect_call--> `propose_fix()`  [INFERRED]
  agent/graph.py → agent/nodes/propose_fix.py
- `build_graph()` --indirect_call--> `revert_and_escalate()`  [INFERRED]
  agent/graph.py → agent/nodes/revert_and_escalate.py
- `build_graph()` --indirect_call--> `risk_gate()`  [INFERRED]
  agent/graph.py → agent/nodes/risk_gate.py

## Import Cycles
- None detected.

## Communities (18 total, 1 thin omitted)

### Community 0 - "SelfHealState"
Cohesion: 0.12
Nodes (27): Central config for the self-heal agent. Everything here is read from the enviro, build_graph(), Wires every node in agent/nodes/ into the StateGraph described in the plan (see, route_classification(), route_retrigger(), route_risk(), route_validation(), main() (+19 more)

### Community 1 - "Setup: building this from zero"
Cohesion: 0.06
Nodes (35): 1.1 Add a `ci` target to `profiles/profiles.yml`, 1.2 Add the `dbt_ci.yml` GitHub Actions workflow, 1.3 Branch protection, 2.1 Add the failure callback, 2.2 IAM: let MWAA workers publish EventBridge events, 3.1 Create the service principal, 3.2 Grant warehouse access, 3.3 Grant Unity Catalog privileges (+27 more)

### Community 2 - "dbt_ai_project"
Cohesion: 0.10
Nodes (18): Code map, Guardrails cheat sheet, Local testing (no AWS infra required), One-time setup (do these before the agent's first real run), Self-heal agent, Day-to-day, infra/ -- Terraform for the self-heal agent's AWS resources, One-time bootstrap: remote state (+10 more)

### Community 3 - "github_client.py"
Cohesion: 0.17
Nodes (21): open_pr_and_merge(), Only reached after a merge actually happened but the DAG still     failed on re, revert_and_escalate(), add_label(), _client(), clone_url_with_token(), get_merge_commit_sha(), _installation_auth() (+13 more)

### Community 4 - "Troubleshooting a failed deploy-agent.yml run"
Cohesion: 0.10
Nodes (21): End-to-end test: breaking a model on purpose, Error: `AccessDeniedException` on `logs:DescribeLogGroups`, Error: `Invocation of model ID ... with on-demand throughput isn't supported`, Error: `must contain only printable ASCII characters` from `aws iam put-role-policy`, Error: `not authorized to perform: ecr:GetAuthorizationToken`, Error: `Not authorized to perform sts:AssumeRoleWithWebIdentity`, Error: `reading Lambda Function ... code signing config ... AccessDeniedException`, Error: `The value cannot be empty or all whitespace` on `backend "s3" {}` (+13 more)

### Community 5 - "repo_tools.py"
Cohesion: 0.14
Nodes (18): propose_fix(), Every check here is independent -- any single failing check is     enough to ro, risk_gate(), branch_exists_locally(), changed_files(), checkout(), clone_repo(), commit() (+10 more)

### Community 6 - "Concepts"
Cohesion: 0.11
Nodes (18): Airflow / MWAA, Concepts, Cross-region inference profiles (Bedrock), dbt: models, sources, targets, EventBridge as a decoupling layer, GitHub Apps vs. personal access tokens, Idempotency, Infrastructure as Code / Terraform remote state (+10 more)

### Community 7 - "dbt_common.py"
Cohesion: 0.13
Nodes (13): _find_dbt_project_root(), _load_dbt_credentials(), make_dbt_callable(), _mwaa_config_env_name(), notify_self_heal_agent(), Shared configuration and helpers for the dbt Airflow DAGs (dbt_silver_dag.py, d, dbt.databricks_host -> AIRFLOW__DBT__DATABRICKS_HOST, Resolve Databricks credentials for the dbt subprocess.      Prefers MWAA Airfl (+5 more)

### Community 8 - "Architecture"
Cohesion: 0.13
Nodes (15): 1. The problem this solves, 2. Data architecture: the medallion pipeline being protected, 3.1 Failure detection (Airflow → EventBridge), 3.2 Dispatch + circuit breaker (EventBridge → Lambda → Fargate), 3.3 The LangGraph agent, 3.4 The hard backstop: PR + required CI check, 3.5 Verifying the fix actually worked, 3. Self-healing system, end to end (+7 more)

### Community 9 - "MwaaClient"
Cohesion: 0.21
Nodes (6): _cloudwatch_run_id(), MwaaClient, Thin wrapper around the Amazon MWAA-hosted Airflow REST API.  MWAA doesn't exp, POSTs a new manual dagRun. Returns the new run's dag_run_id., Polls until the run leaves queued/running, or timeout_s elapses.         Return, Returns (web_server_hostname, session_cookie).

### Community 10 - "introspect_schema.py"
Cohesion: 0.39
Nodes (6): _find_model_file(), introspect_schema(), _connect(), describe_table(), Read-only Databricks schema introspection, used by the propose_fix node so the, Returns DESCRIBE TABLE output as a compact "col_name: type" listing.      Neve

### Community 11 - "validate_fix.py"
Cohesion: 0.43
Nodes (6): validate_fix(), build_and_test(), DbtResult, Runs dbt inside the agent's own repo clone (see tools/repo_tools.py), against t, `model+` builds the model and everything downstream of it, so a fix     that re, run_dbt()

### Community 12 - "handler.py"
Cohesion: 0.43
Nodes (6): _claim_attempt(), handler(), _notify_slack(), Dispatcher Lambda ================= Sits between the EventBridge "DbtTaskFaile, Atomically claim today's attempt slot. Returns True if claimed (i.e.     this i, _start_agent_task()

## Knowledge Gaps
- **90 isolated node(s):** `startup.sh script`, `Documentation`, `Prerequisites`, `Setup`, `Project structure` (+85 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Setup: building this from zero` connect `Setup: building this from zero` to `dbt_ai_project`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `Operations runbook` connect `Troubleshooting a failed deploy-agent.yml run` to `dbt_ai_project`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `Concepts` connect `Concepts` to `dbt_ai_project`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `SelfHealState` (e.g. with `build_graph()` and `ErrorClassification`) actually correct?**
  _`SelfHealState` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `build_graph()` (e.g. with `route_classification()` and `route_retrigger()`) actually correct?**
  _`build_graph()` has 18 INFERRED edges - model-reasoned connections that need verification._
- **What connects `startup.sh script`, `Documentation`, `Prerequisites` to the rest of the system?**
  _90 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `SelfHealState` be split into smaller, more focused modules?**
  _Cohesion score 0.12367149758454106 - nodes in this community are weakly interconnected._
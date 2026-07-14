# Concepts

Background on every non-obvious technology/pattern used in this project, for
anyone following [`SETUP.md`](./SETUP.md) who wants to understand *why*, not
just copy commands.

## Table of contents

- [Medallion architecture](#medallion-architecture)
- [dbt: models, sources, targets](#dbt-models-sources-targets)
- [Airflow / MWAA](#airflow--mwaa)
- [LangGraph and the ReAct pattern](#langgraph-and-the-react-pattern)
- [Why a graph instead of one big prompt](#why-a-graph-instead-of-one-big-prompt)
- [EventBridge as a decoupling layer](#eventbridge-as-a-decoupling-layer)
- [The circuit breaker pattern](#the-circuit-breaker-pattern)
- [OIDC vs. static AWS keys](#oidc-vs-static-aws-keys)
- [OAuth M2M (machine-to-machine)](#oauth-m2m-machine-to-machine)
- [GitHub Apps vs. personal access tokens](#github-apps-vs-personal-access-tokens)
- [Least privilege, applied](#least-privilege-applied)
- [Idempotency](#idempotency)
- [Required status checks as a hard backstop](#required-status-checks-as-a-hard-backstop)
- [Cross-region inference profiles](#cross-region-inference-profiles-bedrock)
- [Infrastructure as Code / Terraform remote state](#infrastructure-as-code--terraform-remote-state)

---

## Medallion architecture

A data-layering convention popularized by Databricks: raw data lands
untouched in a **Bronze** layer, gets cleaned/deduplicated/validated into a
**Silver** layer, and gets aggregated into business-ready **Gold** marts.
Each layer is a dbt model selector in this project (`--select silver`,
`--select gold`), and each layer lives in its own schema
(`ai_project.silver`, `ai_project.gold`) so permissions, lineage, and
"how trustworthy is this table" are all visible from the schema name alone.

Why it matters here: it's what gives the self-heal agent a *safe place to
practice*. The `ci` target reuses the exact same layering
(`agent_ci_silver`, `agent_ci_gold`) so a candidate fix builds through
realistic multi-layer dependencies, without ever being able to write to the
real `silver`/`gold` schemas.

## dbt: models, sources, targets

- A **model** is a `.sql` file — dbt compiles it (resolving `ref()`/`source()`
  calls to fully-qualified table names) and runs it against your warehouse.
- A **source** (`models/sources.yml`) declares an upstream table dbt doesn't
  manage — usually the raw bronze layer — so models can reference it via
  `{{ source('bronze', 'table_name') }}` instead of hardcoding a schema name.
- A **target** (`profiles.yml`) is a named connection profile — this project
  has `dev`, `prod`, and `ci`. Which target a `dbt run`/`dbt build` uses is
  just a CLI flag (`--target ci`); the SQL and lineage are identical across
  targets, only *where it writes* changes. This is the entire mechanism that
  makes safe agent testing possible — no branching logic anywhere in the
  models themselves.

## Airflow / MWAA

**Apache Airflow** orchestrates dependent tasks on a schedule via **DAGs**
(Directed Acyclic Graphs) — Python files that describe *what* runs and in
what order, not the actual computation. **Amazon MWAA** (Managed Workflows
for Apache Airflow) is AWS's hosted Airflow — you upload DAG files and a
requirements file to an S3 bucket, and AWS runs the scheduler/workers/webserver.

Two Airflow mechanisms this project leans on:

- **`on_failure_callback`** — a function attached to a task (or via
  `default_args` to every task in a DAG) that Airflow calls whenever that
  task fails, given a `context` dict with everything about the failure
  (which DAG, which task, which run, the exception). This is the exact hook
  used to publish the EventBridge event that starts the whole self-heal flow.
- **Airflow configuration options** (`dbt.databricks_host`, etc.) — MWAA's
  mechanism for injecting config into every worker as environment variables
  (`AIRFLOW__DBT__DATABRICKS_HOST`), used instead of Airflow Variables
  because Variables aren't reliably readable from MWAA Airflow 3 workers in
  this setup.

## LangGraph and the ReAct pattern

**ReAct** (Reason + Act) is an LLM agent pattern: instead of asking a model
for one answer in one shot, you let it alternate between *reasoning* (what
should I do next, given what I've seen so far) and *acting* (calling a tool,
reading a result) in a loop, until it reaches a terminal state. This is a
better fit than a single prompt for "diagnose and fix a dbt failure" because
the right fix genuinely depends on information the model doesn't have
up front — it needs to look at the actual failing log, then look at the
actual upstream schema, before it can propose anything.

**LangGraph** is a framework for building exactly this as an explicit state
machine: you define **nodes** (a node here is any Python function
`(state) -> partial_state_update`) and **edges** (including *conditional*
edges — "go to node A if X, node B if Y") over a shared state object. The
graph in this project (`agent/graph.py`) is the ReAct loop made concrete:
`classify → introspect → propose → apply → validate`, with a conditional
edge back to `propose` on failure (bounded by `MAX_FIX_RETRIES`) and forward
through a risk gate on success.

Compared to a hand-rolled while-loop, LangGraph buys three things this
project relies on:

1. **A typed, accumulating state object** (`SelfHealState`) that's the
   natural audit log — every field any node set is still there at the end.
2. **Declarative routing** (`add_conditional_edges`) instead of nested
   if/else scattered through imperative code — the whole control flow is
   visible in one function (`build_graph`), which is also why it was easy to
   draw as the diagram in `ARCHITECTURE.md`.
3. **A `recursion_limit`** as a structural safety net (`agent/main.py` sets
   50) — an upper bound on total node executions per run, independent of
   the semantic `MAX_FIX_RETRIES` bound in `route_validation`.

## Why a graph instead of one big prompt

Three concrete reasons this project chose an explicit multi-node graph over
"send the whole failure to an LLM with a big system prompt and let it decide
everything":

- **Guardrails as code, not as prompt instructions.** "Never write outside
  `models/`" is enforced by `tools/repo_tools.py` rejecting the write, not
  by asking the model nicely. An LLM can be talked out of following an
  instruction; it can't talk its way past code that simply doesn't expose
  the capability.
- **Auditability.** Every node's output is a named, typed field
  (`error_type`, `risk_level`, `validation_passed`, ...). You can point at
  exactly which step made which decision, rather than parsing free text.
- **Cheap, bounded retries.** Only `propose_fix` re-runs on a failed
  validation — not the whole pipeline, and only up to `MAX_FIX_RETRIES`
  times, each retry seeded with a summary of why the last attempt failed.

## EventBridge as a decoupling layer

**Amazon EventBridge** is a pub/sub event bus: producers `PutEvents` with a
`source`/`detail-type`/`detail` payload, and independently-configured
**rules** match on those fields and route to targets (Lambda, in this case).
Using it here instead of having the Airflow callback call the dispatcher
Lambda directly buys:

- **No coupling to invocation mechanics.** The DAG code just describes "a
  failure happened" — it doesn't need to know a Lambda exists, its ARN, or
  how to invoke it. Swapping or adding a second consumer (e.g. a metrics
  pipeline) later is a new EventBridge rule, zero DAG changes.
- **Fire-and-forget from the worker's perspective.** `PutEvents` is a single
  fast API call from inside `on_failure_callback`, which must never block
  the Airflow worker.

## The circuit breaker pattern

Borrowed from resilience engineering: a **circuit breaker** stops repeated
attempts at an operation that's likely to keep failing, instead of retrying
it indefinitely and making things worse (in the classic version: stop
hammering a downstream service that's already down; here: stop re-running
an agent whose last fix for this exact failure didn't actually work).

Implemented here as the simplest possible version — one conditional
DynamoDB write (`ConditionExpression="attribute_not_exists(attempt_key)"`)
keyed by `{dag_id}#{task_id}#{date}`, with a TTL so it self-resets daily.
No half-open state, no failure counting — for "at most one AI-driven fix
attempt per failing task per day," a boolean claim is sufficient, and
simplicity here is a feature: the guardrail itself should be trivially
easy to reason about.

## OIDC vs. static AWS keys

The traditional way to let GitHub Actions call AWS is to generate a static
IAM user access key/secret and paste them into GitHub Secrets. This has two
structural problems: the credential is long-lived (valid until someone
manually rotates it) and it's *bearer* — anyone who gets the secret value
can use it from anywhere, with no way to verify it's really coming from
your CI.

**OIDC (OpenID Connect)** federation fixes both: GitHub's Actions runner
mints a short-lived, cryptographically signed JSON Web Token (JWT) for each
job, asserting claims like *"this is a run of `owner/repo`, on branch
`main`"* (the `sub` claim). AWS's IAM OIDC identity provider verifies that
signature against GitHub's public keys and, if the role's **trust policy**
condition matches the claims presented, issues temporary credentials
(`sts:AssumeRoleWithWebIdentity`) — valid for that one job, then gone. No
secret ever sits in GitHub; the "credential" is really just *proof of who
you are*, checked fresh every run.

This is why the trust policy's `StringLike` condition on
`token.actions.githubusercontent.com:sub` matters so much (see
[`OPERATIONS.md`](./OPERATIONS.md#error-not-authorized-to-perform-stsassumerolewithwebidentity)) —
it's the entire authorization boundary, not a formality.

## OAuth M2M (machine-to-machine)

Most people are familiar with OAuth as "log in with Google" — a *user*
authorizing an app. **OAuth M2M** is the same protocol family used for a
*service* authenticating as itself, with no human/browser involved: a
**client id** (identifies the service principal, not secret) and **client
secret** (proves it), exchanged directly for a short-lived access token via
a `client_credentials` grant.

This project uses it for the Databricks CI service principal instead of a
personal access token (PAT) for two reasons: a PAT is usually tied to a
human user's identity/lifecycle (it dies if that person leaves, and audit
logs show a person's name for automated actions), and Databricks explicitly
recommends OAuth M2M for service principals used in CI/CD. Practically,
`profiles/profiles.yml`'s `ci` target sets `auth_type: oauth` with
`client_id`/`client_secret` instead of `token`, and the Python side
(`agent/tools/databricks_introspect.py`) uses
`databricks.sdk.core.Config(auth_type="oauth-m2m", ...)` to get a
credentials provider instead of a bare token string.

## GitHub Apps vs. personal access tokens

A **GitHub App** is its own first-class identity — not tied to any human
account — installed on specific repos with specific, narrow permissions
(here: Contents, Pull requests, Checks — nothing else) and authenticating
via a private key it alone holds (exchanged for short-lived installation
tokens at call time, not used directly). This is why the agent uses one
instead of a maintainer's personal access token: a PAT inherits *all* of
that person's permissions across every repo/org they can touch, is tied to
their account lifecycle, and shows up in audit logs as that person's action
even when a bot did it. The GitHub App shows up as itself in every PR/commit
it touches, and physically cannot act outside the one repo it's installed on.

## Least privilege, applied

"Least privilege" means every credential/role in the system can do exactly
the operations it needs and nothing else — not as an abstract security
principle, but as a concrete design constraint applied at every layer here:

- The dispatcher Lambda's IAM role can `PutItem` one specific DynamoDB table,
  `RunTask` one specific task definition, `PassRole` for exactly the two
  roles that task definition uses, and read exactly one secret. It cannot
  list other Lambda functions, read other secrets, or start arbitrary tasks.
- The agent's task role can invoke exactly one Bedrock model, read/write its
  own circuit-breaker rows, and read (not write) MWAA — no S3, no RDS, no
  IAM, no broad ECS access.
- The Databricks service principal can `SELECT` bronze and own the
  `agent_ci*` schemas — nothing on real `silver`/`gold`.
- The GitHub App can touch Contents/PRs/Checks on one repo — no admin, no
  other repos.

The payoff: if any single credential leaked or any single component had a
bug, the *blast radius* is small and enumerable, not "everything this AWS
account/GitHub org can do."

## Idempotency

An operation is **idempotent** if running it multiple times has the same
effect as running it once. This matters anywhere retries are possible:

- The dispatcher's circuit-breaker claim uses a conditional write
  specifically so a duplicate EventBridge delivery (EventBridge doesn't
  guarantee exactly-once delivery) can't start two agent runs for the same
  failure.
- `dbt build`/`dbt run` are inherently idempotent (`CREATE OR REPLACE`
  semantics) — re-running the exact same model produces the same table,
  which is what makes it safe for the agent to retry `apply_fix`/`validate_fix`
  multiple times against the same `ci` schema without cleanup logic.

## Required status checks as a hard backstop

GitHub's **branch protection** can require specific CI check(s) to pass
before a PR is *mergeable at all* — enforced by GitHub itself, not by
whichever code is calling the merge API. This project's `risk_gate` decides
which PRs are worth auto-merging *without a human clicking approve*, but
`open_pr_and_merge` still polls the Checks API and refuses to call
`merge_pull_request` unless `dbt-build` is actually green on that commit.
The distinction matters: `risk_gate` is a convenience filter that could have
a bug; the required check is what actually prevents a broken merge,
independent of anything the agent's own code decides.

## Cross-region inference profiles (Bedrock)

Some Bedrock foundation models — including Claude Sonnet 4.5 — aren't
invocable via plain on-demand `InvokeModel` on the bare model id at all;
Anthropic/AWS require going through a **cross-region inference profile**
(model ids prefixed `us.`, `eu.`, or `global.`), which transparently routes
each request to whichever region within that geography currently has
capacity, for better throughput/availability at scale. Practically this
means: use `us.anthropic.claude-sonnet-4-5-...` as the model id, not
`anthropic.claude-sonnet-4-5-...`, and grant IAM `bedrock:InvokeModel` on
both the inference-profile ARN *and* `foundation-model/*` in every region
the profile might route to (see
[`OPERATIONS.md`](./OPERATIONS.md#error-invocation-of-model-id--with-on-demand-throughput-isnt-supported)).

## Infrastructure as Code / Terraform remote state

**Terraform** describes infrastructure declaratively (`.tf` files) and
tracks what it created in a **state file**, so subsequent runs know what
already exists vs. what needs creating/updating/destroying. Running it from
a human's laptop, that state file can just sit on disk. Running it from
GitHub Actions, every job starts on a brand-new, empty runner with no memory
of any previous run — so the state has to live somewhere persistent and
shared: an S3 bucket (with a DynamoDB table for locking, so two concurrent
`terraform apply` runs can't corrupt each other's state). This is the entire
reason [`SETUP.md` §6.3](./SETUP.md#63-terraform-remote-state-backend-one-time-by-hand)
exists as a manual, one-time, non-Terraform-managed step — Terraform can't
be the tool that creates the bucket it stores its own state in.

## Related reading

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — how these pieces fit together in this specific system.
- [`SETUP.md`](./SETUP.md) — building it.
- [`OPERATIONS.md`](./OPERATIONS.md) — running and debugging it.

"""
GitHub operations via a GitHub App identity (not a personal access token --
see agent/README.md for why: repo-scoped permissions, and PRs/commits show
up as authored by the app/bot rather than a human's account).
"""
from __future__ import annotations

import time

from github import Auth, Github

from agent import config


def _client() -> Github:
    auth = Auth.AppAuth(config.GITHUB_APP_ID, config.GITHUB_APP_PRIVATE_KEY).get_installation_auth(
        int(config.GITHUB_APP_INSTALLATION_ID)
    )
    return Github(auth=auth)


def _repo():
    return _client().get_repo(f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}")


def clone_url_with_token() -> str:
    """An https clone URL carrying a fresh installation token, for
    tools.repo_tools.clone_repo / commit_and_push to authenticate with
    (installation tokens are short-lived, ~1h -- generated fresh per run,
    never stored)."""
    auth = Auth.AppAuth(config.GITHUB_APP_ID, config.GITHUB_APP_PRIVATE_KEY).get_installation_auth(
        int(config.GITHUB_APP_INSTALLATION_ID)
    )
    token = auth.token
    return f"https://x-access-token:{token}@github.com/{config.GITHUB_OWNER}/{config.GITHUB_REPO}.git"


def open_pull_request(branch_name: str, title: str, body: str) -> tuple[int, str]:
    repo = _repo()
    pr = repo.create_pull(title=title, body=body, head=branch_name, base=config.GITHUB_DEFAULT_BRANCH)
    return pr.number, pr.html_url


def add_label(pr_number: int, label: str) -> None:
    repo = _repo()
    try:
        repo.get_label(label)
    except Exception:  # noqa: BLE001 - label doesn't exist yet, create it once
        repo.create_label(name=label, color="d93f0b", description="Opened by the dbt self-heal agent")
    repo.get_pull(pr_number).add_to_labels(label)


def request_human_review_comment(pr_number: int, reason: str) -> None:
    repo = _repo()
    repo.get_pull(pr_number).create_issue_comment(
        f":no_entry_sign: **Not auto-merging.** {reason}\n\nA human needs to review and merge this manually."
    )


def wait_for_required_check(pr_number: int, check_name: str, timeout_s: int, poll_interval_s: int) -> bool:
    """Polls the PR's head commit check-runs until `check_name` finishes.
    Returns True only if it concluded 'success'."""
    repo = _repo()
    pr = repo.get_pull(pr_number)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        commit = repo.get_commit(pr.head.sha)
        runs = {run.name: run for run in commit.get_check_runs()}
        run = runs.get(check_name)
        if run and run.status == "completed":
            return run.conclusion == "success"
        time.sleep(poll_interval_s)
    return False


def merge_pull_request(pr_number: int, commit_message: str) -> bool:
    repo = _repo()
    pr = repo.get_pull(pr_number)
    result = pr.merge(commit_message=commit_message, merge_method="squash")
    return bool(result.merged)


def wait_for_workflow_run_on_main(workflow_file: str, after_sha: str, timeout_s: int, poll_interval_s: int) -> bool:
    """After merging, waits for the named GitHub Actions workflow (e.g.
    deploy-mwaa.yml) to complete on `main` for the merge commit or later,
    so retrigger_dag doesn't race the redeploy."""
    repo = _repo()
    workflow = repo.get_workflow(workflow_file)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        runs = workflow.get_runs(branch=config.GITHUB_DEFAULT_BRANCH)
        for run in runs[:5]:
            if run.head_sha == after_sha or run.created_at.timestamp() >= time.time() - timeout_s:
                if run.status == "completed":
                    return run.conclusion == "success"
        time.sleep(poll_interval_s)
    return False


def get_merge_commit_sha(pr_number: int) -> str:
    return _repo().get_pull(pr_number).merge_commit_sha

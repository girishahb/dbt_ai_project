"""
Local filesystem/git operations on a fresh clone of the dbt repo, plus the
three tools exposed to the LLM during propose_fix. `write_file` is the one
hard security boundary in this whole system: it is physically incapable of
writing outside ALLOWED_WRITE_PREFIXES (agent/config.py, default
`models/`), no matter what the model asks for -- tests/, macros/,
dbt_project.yml, profiles/, dags/, mwaa/, .github/ are all off limits by
construction, not just by prompt instruction.
"""
from __future__ import annotations

import os
import subprocess

from agent import config


def clone_repo(clone_url: str, into_dir: str, ref: str = config.GITHUB_DEFAULT_BRANCH) -> str:
    if os.path.isdir(into_dir):
        subprocess.run(["rm", "-rf", into_dir], check=False)
    os.makedirs(os.path.dirname(into_dir) or ".", exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, clone_url, into_dir],
        check=True,
        capture_output=True,
        text=True,
    )
    return into_dir


def create_branch(repo_path: str, branch_name: str) -> None:
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_path, check=True)


def commit(repo_path: str, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=dbt-self-heal-agent", "-c", "user.email=self-heal-agent@users.noreply.github.com",
         "commit", "-m", message, "--allow-empty"],
        cwd=repo_path,
        check=True,
    )


def push(repo_path: str, branch_name: str) -> None:
    subprocess.run(["git", "push", "origin", branch_name], cwd=repo_path, check=True)


def branch_exists_locally(repo_path: str, branch_name: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def checkout(repo_path: str, branch_name: str) -> None:
    subprocess.run(["git", "checkout", branch_name], cwd=repo_path, check=True)


def pull(repo_path: str, branch_name: str) -> None:
    subprocess.run(["git", "pull", "origin", branch_name], cwd=repo_path, check=True)


def revert_commit(repo_path: str, sha: str) -> None:
    """`-m 1` because this always reverts a squash-merge commit, which has
    a single parent on main -- there's no merge-parent ambiguity to resolve."""
    subprocess.run(
        ["git", "-c", "user.name=dbt-self-heal-agent", "-c", "user.email=self-heal-agent@users.noreply.github.com",
         "revert", "--no-edit", "-m", "1", sha],
        cwd=repo_path,
        check=True,
    )


def _resolve_and_check(repo_path: str, relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if not normalized.startswith(config.ALLOWED_WRITE_PREFIXES):
        raise PermissionError(
            f"Refusing to write to {relative_path!r}: only paths under "
            f"{config.ALLOWED_WRITE_PREFIXES} may be modified by the agent."
        )
    full_path = os.path.abspath(os.path.join(repo_path, normalized))
    repo_root = os.path.abspath(repo_path)
    if os.path.commonpath([full_path, repo_root]) != repo_root:
        raise PermissionError(f"Refusing to write outside the repo clone: {relative_path!r}")
    return full_path


def read_file(repo_path: str, relative_path: str) -> str:
    full_path = os.path.abspath(os.path.join(repo_path, relative_path.lstrip("/")))
    try:
        with open(full_path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        return f"(could not read {relative_path}: {exc})"


def list_dir(repo_path: str, relative_path: str = ".") -> str:
    full_path = os.path.abspath(os.path.join(repo_path, relative_path.lstrip("/")))
    try:
        return "\n".join(sorted(os.listdir(full_path)))
    except OSError as exc:
        return f"(could not list {relative_path}: {exc})"


def write_file(repo_path: str, relative_path: str, content: str) -> str:
    full_path = _resolve_and_check(repo_path, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"wrote {len(content)} bytes to {relative_path}"


def diff_stat(repo_path: str) -> tuple[int, int]:
    """Returns (files_changed, total_line_changes) vs the branch point -- fed into risk_gate."""
    result = subprocess.run(
        ["git", "diff", "--numstat", f"origin/{config.GITHUB_DEFAULT_BRANCH}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    total_changes = 0
    for line in lines:
        added, removed, _path = line.split("\t")
        total_changes += (int(added) if added != "-" else 0) + (int(removed) if removed != "-" else 0)
    return len(lines), total_changes


def changed_files(repo_path: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{config.GITHUB_DEFAULT_BRANCH}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def full_diff(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"origin/{config.GITHUB_DEFAULT_BRANCH}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout

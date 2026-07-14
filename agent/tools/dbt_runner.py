"""
Runs dbt inside the agent's own repo clone (see tools/repo_tools.py),
against the isolated `ci` target -- never anything that could touch prod.
Same pattern as the dbt_ci.yml GitHub Actions workflow, just invoked from
Python instead of a shell step, so validate_fix can capture stdout/stderr
directly instead of parsing GitHub Actions logs.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from agent import config


@dataclass
class DbtResult:
    success: bool
    output: str


def run_dbt(repo_path: str, subcommand: str, select: str) -> DbtResult:
    cmd = [
        "dbt",
        subcommand,
        "--select",
        select,
        "--target",
        config.DBT_CI_TARGET,
        "--project-dir",
        config.DBT_PROJECT_SUBDIR,
        "--profiles-dir",
        config.DBT_PROFILES_SUBDIR,
    ]
    result = subprocess.run(
        cmd,
        cwd=repo_path,
        text=True,
        capture_output=True,
        timeout=600,
    )
    output = result.stdout + "\n" + result.stderr
    return DbtResult(success=result.returncode == 0, output=output)


def build_and_test(repo_path: str, model: str) -> DbtResult:
    """`model+` builds the model and everything downstream of it, so a fix
    that resolves the original failure but breaks something built on top of
    it is caught here too, not just in the next scheduled MWAA run."""
    build = run_dbt(repo_path, "build", f"{model}+")
    return build

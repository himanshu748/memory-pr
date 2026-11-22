"""The bundled GitHub Action runs `mempr check` through a shell step; run that step as written."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from mempr.report import MARKER

ACTION = Path(__file__).resolve().parents[1] / "action.yml"


def review_step() -> str:
    steps = yaml.safe_load(ACTION.read_text("utf-8"))["runs"]["steps"]
    return next(s["run"] for s in steps if s.get("id") == "review")


def run_step(repo, tmp_path, head):
    out = tmp_path / "runner"
    out.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(out),
        "GITHUB_OUTPUT": str(out / "output"),
        "GITHUB_STEP_SUMMARY": str(out / "summary"),
        "MEMPR_BASE": "main",
        "MEMPR_HEAD": head,
        "MEMPR_VAULT": "memory",
        "MEMPR_RULES": "memory-rules.yaml",
    }
    subprocess.run(["bash", "-c", review_step()], cwd=repo.path, env=env, check=True)
    comment = out / "mempr-comment.md"
    return (out / "output").read_text().strip(), comment.read_text() if comment.exists() else ""


@pytest.mark.parametrize(
    ("value", "code", "verdict"),
    [("mysql-8", "code=1", "blocked"), (None, "code=0", "passed")],
)
def test_the_action_step_reviews_and_writes_the_comment(repo, tmp_path, value, code, verdict):
    repo.branch("agent/change")
    if value:
        repo.add("payments-api", "database", value)
    else:
        repo.add("ledger-worker", "queue", "kafka")
    repo.commit("change memory")
    output, comment = run_step(repo, tmp_path, "agent/change")
    assert output == code
    assert comment.startswith(MARKER)
    assert f"Memory review: {verdict}" in comment
    assert (tmp_path / "runner" / "summary").read_text() == comment


def test_the_action_step_fails_without_a_comment_on_a_bad_ref(repo, tmp_path):
    output, comment = run_step(repo, tmp_path, "no-such-ref")
    assert output == "code=2"
    assert comment == ""

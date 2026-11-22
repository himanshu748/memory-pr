"""The page's "Write a memory" drafts get the same verdict from `mempr check` as from the page.

site/data/playground-cases.json lists drafts and the verdict each must get. The page reviews
them with Relate in the browser; here the same drafts are written with `mempr.memory` and
reviewed with `mempr.check`, so the two cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mempr.check import review, review_vaults
from mempr.playground import apply, clean, review_request
from mempr.rules import load_rules
from mempr.vault import note_files

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "site" / "data" / "playground-cases.json").read_text())["cases"]
_RULES_FILE = ROOT / "memory-rules.yaml"
RULES = load_rules(_RULES_FILE.read_text() if _RULES_FILE.exists() else None)


def _memory_digest() -> str:
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "demo" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.memory_digest()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def page_memory(tmp_path_factory) -> Path:
    """A repository whose `main` holds the memory the page was built from."""
    committed = json.loads((ROOT / "site" / "data" / "demo.json").read_text())
    if committed["memory_digest"] != _memory_digest():
        pytest.skip("memory/ changed since site/data/demo.json was built")
    repo = tmp_path_factory.mktemp("playground") / "repo"
    for note in note_files(ROOT / "memory"):
        target = repo / "memory" / note.relative_to(ROOT / "memory")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(note, target)
    shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "memory")
    return repo


def test_the_starters_are_among_the_cases():
    starters = [case for case in CASES if case.get("starter")]
    assert len(starters) == 3
    assert [case["blocked"] for case in starters] == [True, True, False]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_mempr_check_reaches_the_verdict_the_page_shows(page_memory, case):
    repo = page_memory
    git(repo, "checkout", "-q", "-B", f"you/{case['id']}", "main")
    apply(repo / "memory", clean(case["draft"]))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"memory: {case['id']}")
    result = review(repo, "main", f"you/{case['id']}", "memory", RULES)
    assert sorted({finding.rule.id for finding in result.blocking}) == sorted(case["rules"])
    assert result.blocked == case["blocked"]
    assert result.problem_count == len(result.blocking)  # nothing but rule findings


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_review_without_git_reaches_the_same_verdict(page_memory, case, tmp_path):
    """The server's path: two vault folders, no repository."""
    git(page_memory, "checkout", "-q", "main")
    after = tmp_path / "after"
    shutil.copytree(page_memory / "memory", after)
    apply(after, clean(case["draft"]))
    result = review_vaults(page_memory / "memory", after, RULES)
    assert sorted({finding.rule.id for finding in result.blocking}) == sorted(case["rules"])
    assert result.blocked == case["blocked"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_review_endpoint_reaches_the_same_verdict(case):
    body = review_request({"draft": case["draft"]}, ROOT / "memory", RULES)
    assert (body["blocked"], body["rules"]) == (case["blocked"], sorted(case["rules"]))

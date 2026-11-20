"""The page's "Write a memory" drafts get the same verdict from `mempr check` as from the page.

site/data/playground-cases.json lists drafts and the verdict each must get. The page reviews
them with Relate in the browser; here the same drafts are written with `mempr.memory` and
reviewed with `mempr.check`, so the two cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mempr import memory
from mempr.check import review
from mempr.rules import load_rules
from mempr.vault import note_files

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "site" / "data" / "playground-cases.json").read_text())["cases"]


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


def write(vault: Path, draft: dict) -> None:
    """Make the change the page's form describes."""
    source = draft.get("source") or "a-source-that-is-removed-again"
    name = memory.add_fact(
        vault,
        entity=draft["entity"],
        predicate=draft["predicate"],
        value=draft["value"],
        agent=draft["agent"],
        source=source,
        supersedes=draft.get("supersedes"),
    )
    if not draft.get("source"):  # "No source" on the form: the note has no SOURCE link
        note = vault / "Fact" / f"{name}.md"
        note.write_text(re.sub(r"^SOURCE:\n(  - .*\n)+", "", note.read_text(), flags=re.M))
        (vault / "Source" / f"{memory.slug(source)}.md").unlink()
    for fact in draft.get("retract", []):
        memory.retract(vault, fact)


def test_the_starters_are_among_the_cases():
    starters = [case for case in CASES if case.get("starter")]
    assert len(starters) == 3
    assert [case["blocked"] for case in starters] == [True, True, False]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_mempr_check_reaches_the_verdict_the_page_shows(page_memory, case):
    repo = page_memory
    git(repo, "checkout", "-q", "-B", f"you/{case['id']}", "main")
    write(repo / "memory", case["draft"])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"memory: {case['id']}")
    rules_file = ROOT / "memory-rules.yaml"
    rules = load_rules(rules_file.read_text() if rules_file.exists() else None)
    result = review(repo, "main", f"you/{case['id']}", "memory", rules)
    assert sorted({finding.rule.id for finding in result.blocking}) == sorted(case["rules"])
    assert result.blocked == case["blocked"]
    assert result.problem_count == len(result.blocking)  # nothing but rule findings

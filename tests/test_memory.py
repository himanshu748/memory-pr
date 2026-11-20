from __future__ import annotations

import pytest

from mempr import memory
from mempr.cli import main
from mempr.vault import lint


def test_recall_returns_current_facts_with_sources(repo):
    facts = memory.recall(repo.vault, "payments-api")
    assert {(f["predicate"], f["value"]) for f in facts} == {
        ("database", "postgres-15"),
        ("migration-tool", "pg-migrate"),
    }
    assert all(f["sources"] == ["docs-payments-api-md"] for f in facts)


def test_recall_hides_superseded_and_retracted_facts(repo):
    repo.add(
        "payments-api", "database", "mysql-8", supersedes=["payments-api--database--postgres-15"]
    )
    memory.retract(repo.vault, "payments-api--migration-tool--pg-migrate")
    facts = memory.recall(repo.vault, "payments-api")
    assert [(f["predicate"], f["value"]) for f in facts] == [("database", "mysql-8")]


def test_recall_of_unknown_entity_is_empty_and_writes_nothing(repo, capsys):
    before = repo.git("status", "--porcelain")
    assert main(["--repo", str(repo.path), "recall", "nope"]) == 0
    assert "Do not guess" in capsys.readouterr().out
    assert repo.git("status", "--porcelain") == before


def test_retract_unknown_fact_fails(repo):
    with pytest.raises(KeyError):
        memory.retract(repo.vault, "missing")


def test_notes_written_by_cypher_are_clean_files(repo):
    issues, ids = lint(repo.vault)
    assert issues == []
    assert "Fact/payments-api--database--postgres-15" in ids
    assert "Source/docs-payments-api-md" in ids


def test_cli_check_exit_code_and_outputs(repo, tmp_path, capsys):
    repo.branch("agent/bad")
    repo.add("payments-api", "database", "mysql-8")
    repo.commit("bad")
    out, js = tmp_path / "c.md", tmp_path / "c.json"
    code = main(
        [
            "--repo",
            str(repo.path),
            "check",
            "--base",
            "main",
            "--head",
            "agent/bad",
            "--out",
            str(out),
            "--json",
            str(js),
        ]
    )
    assert code == 1
    assert "blocked (1 problem)" in out.read_text()
    assert '"blocked": true' in js.read_text()

from __future__ import annotations

import pytest

from mempr import memory
from mempr.cli import main
from mempr.memory import add_fact, recall
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


def test_cli_check_reports_unrelated_histories_without_a_traceback(repo, capsys):
    repo.git("checkout", "-q", "--orphan", "agent/unrelated")
    repo.git("rm", "-q", "-rf", ".")
    (repo.path / "note.txt").write_text("unrelated history\n")
    repo.commit("orphan")

    code = main(
        [
            "--repo",
            str(repo.path),
            "check",
            "--base",
            "main",
            "--head",
            "agent/unrelated",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "mempr check: no common ancestor between" in captured.err
    assert "Traceback" not in captured.err


def _add(repo, value, **kwargs):
    return add_fact(
        repo.vault,
        entity="payments-api",
        predicate="language",
        value=value,
        agent="scout",
        source="docs/payments-api.md",
        **kwargs,
    )


def test_values_that_differ_only_in_punctuation_stay_separate_facts(repo):
    assert _add(repo, "C++") != _add(repo, "C#")
    values = {
        r["value"] for r in recall(repo.vault, "payments-api") if r["predicate"] == "language"
    }
    assert values == {"C++", "C#"}


def test_a_fact_keeps_its_note_after_the_value_it_collided_with_is_deleted(repo):
    first, second = _add(repo, "C++"), _add(repo, "C#")
    (repo.vault / "Fact" / f"{first}.md").unlink()
    assert _add(repo, "C#") == second
    values = [
        r["value"] for r in recall(repo.vault, "payments-api") if r["predicate"] == "language"
    ]
    assert values == ["C#"]


def test_going_back_to_an_earlier_value_recalls_that_value(repo):
    names = [_add(repo, "go")]
    for value in ("rust", "zig", "java"):
        names.append(_add(repo, value, supersedes=[names[-1]]))
    assert _add(repo, "go", supersedes=[names[-1]]) == names[0]
    values = [
        r["value"] for r in recall(repo.vault, "payments-api") if r["predicate"] == "language"
    ]
    assert values == ["go"]


def test_a_value_keeps_its_note_name_when_stated_again(repo):
    first = _add(repo, "python-3.12")
    assert first == "payments-api--language--python-3-12"
    assert _add(repo, "python-3.12") == first
    assert _add(repo, "python-3-12") != first
    values = {
        r["value"] for r in recall(repo.vault, "payments-api") if r["predicate"] == "language"
    }
    assert values == {"python-3.12", "python-3-12"}


def test_linking_to_a_fact_that_does_not_exist_writes_nothing(repo):
    with pytest.raises(KeyError):
        _add(repo, "go", derived_from=["payments-api--database--mysql-8"])
    assert repo.git("status", "--porcelain", "--", "memory/Fact") == ""


def test_two_sources_with_the_same_note_name_are_refused(repo):
    with pytest.raises(ValueError):
        add_fact(
            repo.vault,
            entity="payments-api",
            predicate="language",
            value="go",
            agent="scout",
            source="docs/payments-api-md",
        )


@pytest.mark.parametrize(
    ("rules", "error"),
    [
        ("- id: [broken", "invalid YAML"),
        ("rules: []", "expected a list of review rules"),
        ("- id: custom\n  severity: block\n", "'title' must be a string"),
    ],
)
def test_check_reports_invalid_rule_configuration(repo, capsys, rules, error):
    (repo.path / "memory-rules.yaml").write_text(rules, "utf-8")
    repo.commit("add invalid rules")
    assert main(["--repo", str(repo.path), "check", "--base", "main", "--head", "main"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "memory-rules.yaml" in captured.err
    assert error in captured.err
    assert "Traceback" not in captured.err


def test_rules_are_read_from_the_base_branch(repo, capsys):
    repo.git("checkout", "-q", "-b", "agent/sneaky")
    (repo.path / "memory-rules.yaml").write_text(
        "rules:\n  - id: contradiction\n    severity: warn\n    title: off\n"
        "    message: off\n    cypher: RETURN 1 AS x LIMIT 0\n"
    )
    add_fact(
        repo.vault,
        entity="payments-api",
        predicate="database",
        value="mysql-8",
        agent="scout",
        source="docs/payments-api.md",
    )
    repo.commit("contradict and switch the rule off")
    code = main(["--repo", str(repo.path), "check", "--base", "main", "--head", "agent/sneaky"])
    assert code == 1
    assert "Contradicts a fact already in memory" in capsys.readouterr().out

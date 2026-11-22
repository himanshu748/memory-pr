from __future__ import annotations

import pytest

from mempr import gitutil
from mempr.check import review, three_way
from mempr.memory import add_fact, retract
from mempr.report import MARKER, render
from mempr.rules import default_rules

DB = "payments-api--database--postgres-15"


def run(repo, head, base="main"):
    return review(repo.path, base, head, "memory", default_rules())


def rule_ids(result):
    return [f.rule.id for f in result.findings]


def test_new_sourced_fact_passes(repo):
    repo.branch("agent/ok")
    repo.add("ledger-worker", "queue", "kafka")
    repo.commit("learn queue")
    result = run(repo, "agent/ok")
    assert not result.blocked
    assert ("node", "Fact/ledger-worker--queue--kafka") in result.added
    assert "passed" in render(result)


def test_contradiction_is_blocked_with_the_path(repo):
    repo.branch("agent/bad")
    repo.add("payments-api", "database", "mysql-8", agent="builder")
    repo.commit("learn database")
    result = run(repo, "agent/bad")
    assert result.blocked
    assert rule_ids(result) == ["contradiction"]
    text = render(result)
    assert text.startswith(MARKER)
    assert "(Entity/payments-api)<-[:ABOUT]-" in text


def test_contradiction_between_two_branches_git_merges_cleanly(repo):
    repo.branch("agent/a")
    repo.add("checkout-web", "port", "3000")
    repo.commit("a")
    repo.branch("agent/b")
    repo.add("checkout-web", "port", "8080")
    repo.commit("b")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "agent/a", "-m", "merge a")
    result = run(repo, "agent/b")
    assert not result.git_conflicts
    assert rule_ids(result) == ["contradiction"]


def test_superseding_resolves_the_contradiction_but_flags_derived_facts(repo):
    repo.branch("agent/supersede")
    repo.add("payments-api", "database", "mysql-8", supersedes=[DB])
    repo.commit("database moved")
    result = run(repo, "agent/supersede")
    assert rule_ids(result) == ["stale-derivation"]
    row = result.findings[0].row
    assert row["fact"] == "payments-api--migration-tool--pg-migrate"
    assert row["reason"] == "superseded"


def test_superseding_and_retracting_the_derived_fact_passes(repo):
    repo.branch("agent/clean")
    repo.add("payments-api", "database", "mysql-8", supersedes=[DB])
    from mempr import memory

    memory.retract(repo.vault, "payments-api--migration-tool--pg-migrate")
    repo.commit("database moved, migration tool retracted")
    assert not run(repo, "agent/clean").blocked


def test_superseding_and_replacing_the_derived_fact_passes(repo):
    repo.branch("agent/replace")
    new = repo.add("payments-api", "database", "mysql-8", supersedes=[DB]).removeprefix("Fact/")
    repo.add(
        "payments-api",
        "migration-tool",
        "flyway",
        supersedes=["payments-api--migration-tool--pg-migrate"],
        derived_from=[new],
    )
    repo.commit("database moved, migration tool replaced")
    assert not run(repo, "agent/replace").blocked


def test_a_fact_resting_on_a_replaced_derived_fact_is_still_flagged(repo):
    repo.branch("agent/chain")
    repo.add(
        "payments-api",
        "backup-job",
        "pg-dump",
        derived_from=["payments-api--migration-tool--pg-migrate"],
    )
    repo.commit("backup job rests on the migration tool")
    repo.branch("agent/replace-middle", "agent/chain")
    repo.add(
        "payments-api",
        "migration-tool",
        "flyway",
        supersedes=["payments-api--migration-tool--pg-migrate"],
    )
    repo.commit("migration tool replaced")
    result = run(repo, "agent/replace-middle", base="agent/chain")
    assert rule_ids(result) == ["stale-derivation"]
    assert result.findings[0].row["fact"] == "payments-api--backup-job--pg-dump"


def test_existing_problems_on_base_are_not_blamed_on_the_pull_request(repo):
    repo.add("payments-api", "database", "mysql-8")
    repo.commit("contradiction already on main")
    repo.branch("agent/ok")
    repo.add("ledger-worker", "queue", "kafka")
    repo.commit("unrelated")
    assert not run(repo, "agent/ok").blocked


def test_unsourced_fact_and_dangling_link_are_blocked(repo):
    repo.branch("agent/sloppy")
    (repo.vault / "Fact" / "ledger-worker--queue--kafka.md").write_text(
        "---\ntype: Fact\nname: ledger-worker--queue--kafka\npredicate: queue\n"
        'status: active\nvalue: kafka\nABOUT:\n  - "[[Entity/ledger-worker]]"\n---\n'
    )
    repo.commit("hand-written fact")
    result = run(repo, "agent/sloppy")
    assert set(rule_ids(result)) == {"dangling-link", "unsourced-fact", "unattributed-fact"}
    assert result.blocked


def test_clean_git_merge_that_damages_the_graph_is_blocked(repo):
    """Both branches add `reviewed` to one note on different lines: git merges, Relate loses it."""
    note = f"Fact/{DB}"
    repo.branch("agent/a")
    repo.edit(note, "type: Fact\n", "type: Fact\nreviewed: by-agent-a\n")
    repo.commit("a reviews")
    repo.git("checkout", "-q", "main")
    repo.edit(note, "value: postgres-15\n", "value: postgres-15\nreviewed: by-human\n")
    repo.commit("human reviews")
    result = run(repo, "agent/a")
    assert not result.git_conflicts
    assert [(c.node, c.key) for c in result.property_conflicts] == [(note, "reviewed")]
    assert [i.kind for i in result.file_issues] == ["duplicate_key"]
    assert ("rel", note, "ABOUT", "Entity/payments-api", "{}") in result.dropped
    text = render(result)
    assert "silently drop" in text and "set twice" in text


def test_git_conflict_is_reported(repo):
    note = f"Fact/{DB}"
    repo.branch("agent/a")
    repo.edit(note, "status: active", "status: retracted")
    repo.commit("a")
    repo.git("checkout", "-q", "main")
    repo.edit(note, "status: active", "status: verified")
    repo.commit("main")
    result = run(repo, "agent/a")
    assert result.git_conflicts == [f"memory/{note}.md"]
    assert result.blocked


def test_three_way_keeps_removals_and_reports_only_unexpected_losses():
    node, a, b, c = (
        ("node", "N/x"),
        ("rel", "N/x", "R", "N/a", "{}"),
        ("rel", "N/x", "R", "N/b", "{}"),
        ("rel", "N/x", "R", "N/c", "{}"),
    )
    ancestor = frozenset({node, a, b})
    target = frozenset({node, a, b, c})
    head = frozenset({node, a})  # head removed b on purpose
    conflicts, dropped = three_way(ancestor, target, head, frozenset({node, a, c}))
    assert conflicts == [] and dropped == []
    conflicts, dropped = three_way(ancestor, target, head, frozenset({node, a}))
    assert dropped == [c]


def test_a_second_replacement_is_not_a_contradiction_with_the_first_value(repo):
    for value, old in (("postgres-16", "postgres-15"), ("postgres-17", "postgres-16")):
        repo.git("checkout", "-q", "-b", f"agent/{value}", "main")
        add_fact(
            repo.vault,
            entity="payments-api",
            predicate="database",
            value=value,
            agent="scout",
            source="docs/payments-api.md",
            supersedes=[f"payments-api--database--{old}"],
        )
        retract(repo.vault, "payments-api--migration-tool--pg-migrate")
        repo.commit(f"move to {value}")
        result = run(repo, head=f"agent/{value}")
        assert not result.blocked, [f.rule.id for f in result.findings]
        repo.git("checkout", "-q", "main")
        repo.git("merge", "-q", "--no-ff", f"agent/{value}", "-m", f"merge {value}")


def test_facts_that_replace_each_other_in_a_circle_are_blocked(repo):
    repo.branch("agent/circle")
    repo.add("payments-api", "database", "postgres-16", supersedes=[DB])
    repo.edit(
        f"Fact/{DB}",
        "status: active\n",
        'status: active\nSUPERSEDES:\n  - "[[Fact/payments-api--database--postgres-16]]"\n',
    )
    retract(repo.vault, "payments-api--migration-tool--pg-migrate")
    repo.commit("circle")
    assert "supersedes-cycle" in rule_ids(run(repo, head="agent/circle"))


def test_no_common_ancestor_is_a_clear_error(repo):
    repo.git("checkout", "-q", "--orphan", "agent/unrelated")
    repo.git("rm", "-q", "-rf", ".")
    (repo.path / "note.txt").write_text("x")
    repo.commit("orphan")
    repo.git("checkout", "-q", "main")
    with pytest.raises(gitutil.GitError, match="no common ancestor"):
        run(repo, head="agent/unrelated")

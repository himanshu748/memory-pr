from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mempr.cli import main
from mempr.export import export

ROOT = Path(__file__).resolve().parent.parent
DB = "Fact/payments-api--database--postgres-15"
MYSQL = "Fact/payments-api--database--mysql-8"
TOOL = "Fact/payments-api--migration-tool--pg-migrate"


def run(repo, *pulls):
    return export(repo.path, "main", list(pulls), "memory", "memory-rules.yaml")


def by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def rels(graph):
    return {(r["source"], r["type"], r["target"]) for r in graph["rels"]}


def test_memory_is_exported_with_provenance_and_note_text(repo):
    data = run(repo)
    assert data["format"] == 1
    assert data["rev"] == repo.git("rev-parse", "main")
    assert data["pulls"] == []
    nodes = by_id(data["memory"])
    fact = nodes[DB]
    assert fact["label"] == "Fact"
    assert fact["props"]["value"] == "postgres-15"
    assert fact["file"] == f"memory/{DB}.md"
    assert "Stores data in Postgres 15." in fact["text"]
    links = rels(data["memory"])
    assert (DB, "ABOUT", "Entity/payments-api") in links
    assert (DB, "ASSERTED_BY", "Agent/scout") in links
    assert (TOOL, "DERIVED_FROM", DB) in links
    assert {r["id"] for r in data["rules"]} >= {"contradiction", "stale-derivation"}
    assert all(r["cypher"] for r in data["rules"])


def test_contradiction_is_drawn_as_the_two_facts_and_the_path(repo):
    repo.branch("agent/bad")
    repo.add("payments-api", "database", "mysql-8", agent="builder")
    repo.commit("learn database")
    (pull,) = run(repo, "agent/bad")["pulls"]
    assert pull["id"] == "agent/bad"
    assert pull["title"] == "learn database"
    assert pull["blocked"]
    assert "before" not in pull  # same graph as `memory`
    assert MYSQL in by_id(pull["after"])
    assert pull["review"]["blocked"]
    assert pull["comment"].startswith("<!--")
    (problem,) = pull["problems"]
    assert problem["rule"] == "contradiction"
    assert problem["severity"] == "block"
    assert {DB, MYSQL, "Entity/payments-api"} <= set(problem["nodes"])
    assert [DB, "ABOUT", "Entity/payments-api"] in problem["rels"]
    assert [MYSQL, "ABOUT", "Entity/payments-api"] in problem["rels"]


def test_stale_derivation_is_drawn_as_the_chain_to_the_replaced_fact(repo):
    repo.branch("agent/move")
    repo.add("payments-api", "database", "cockroachdb-24", supersedes=[DB.split("/")[1]])
    repo.commit("move database")
    (pull,) = run(repo, "agent/move")["pulls"]
    (problem,) = pull["problems"]
    assert problem["rule"] == "stale-derivation"
    assert {TOOL, DB} <= set(problem["nodes"])
    assert [TOOL, "DERIVED_FROM", DB] in problem["rels"]


def test_clean_pull_has_no_problems_and_explicit_base_keeps_before(repo):
    repo.branch("other")
    repo.add("checkout-web", "port", "3000")
    repo.commit("port")
    repo.branch("agent/ok", "other")
    repo.add("ledger-worker", "queue", "kafka")
    repo.commit("queue")
    (pull,) = run(repo, "other..agent/ok")["pulls"]
    assert not pull["blocked"]
    assert pull["problems"] == []
    assert "Fact/checkout-web--port--3000" in by_id(pull["before"])
    assert "Fact/ledger-worker--queue--kafka" not in by_id(pull["before"])
    assert "Fact/ledger-worker--queue--kafka" in by_id(pull["after"])


def test_dangling_link_is_exported_as_a_stub(repo):
    repo.branch("agent/typo")
    repo.edit(TOOL, DB.split("/")[1], "payments-api--database--postgres-51")
    repo.commit("typo")
    (pull,) = run(repo, "agent/typo")["pulls"]
    stub = by_id(pull["after"])["Fact/payments-api--database--postgres-51"]
    assert stub["stub"] is True
    assert any(
        p.get("rule") == "dangling-link" and stub["id"] in p["nodes"] for p in pull["problems"]
    )


def test_export_does_not_touch_the_checkout(repo):
    run(repo)
    assert repo.git("status", "--porcelain") == ""


def test_cli_writes_json(repo, tmp_path, monkeypatch):
    repo.branch("agent/bad")
    repo.add("payments-api", "database", "mysql-8")
    repo.commit("learn database")
    monkeypatch.chdir(repo.path)
    out = tmp_path / "out.json"
    assert main(["export", "--rev", "main", "--pull", "agent/bad", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["rev"] == repo.git("rev-parse", "main")
    assert [p["id"] for p in data["pulls"]] == ["agent/bad"]


def _build_site():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "demo" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_site_demo_data_shows_every_scenario_and_matches_memory():
    """site/data/demo.json is committed so the page works offline; it must not go stale."""
    built = _build_site().build()
    assert {p["id"]: p["blocked"] for p in built["pulls"]} == {
        "agent/builder-contradiction": True,
        "agent/planner-supersede": True,
        "agent/auditor-stale-clone": True,
        "agent/intern-unsourced": True,
        "agent/mapper-clean": False,
    }
    committed = json.loads((ROOT / "site" / "data" / "demo.json").read_text())
    assert committed == json.loads(json.dumps(built)), (
        "memory/ or the demo agents changed: run `python demo/build_site.py` and commit the result"
    )


def test_pull_with_its_own_base_carries_that_base_rules(repo):
    rule = "- id: no-node\n  title: t\n  message: m\n  severity: block\n  cypher: |\n"
    rule += "    MATCH (f:Fact) WHERE f.value STARTS WITH 'node' RETURN f.name AS fact\n"
    repo.branch("release")
    (repo.path / "memory-rules.yaml").write_text(rule)
    repo.commit("release rule")
    repo.branch("agent/ok", "release")
    repo.add("ledger-worker", "runtime", "node-20")
    repo.commit("runtime")
    data = run(repo, "release..agent/ok")
    (pull,) = data["pulls"]
    assert "no-node" not in {r["id"] for r in data["rules"]}
    assert "no-node" in {r["id"] for r in pull["rules"]}
    assert any(p.get("rule") == "no-node" for p in pull["problems"])

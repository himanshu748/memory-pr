"""The committed real-repository measurement agrees with its Relate store and its claims."""

import json
from pathlib import Path

from mempr.check import review_vaults
from mempr.rules import default_rules
from mempr.vault import open_vault, rows

ROOT = Path(__file__).resolve().parent.parent
REPORT = json.loads((ROOT / "docs" / "real-repos.json").read_text())
VAULT = ROOT / "bench" / "real-memory"


def test_report_matches_its_repositories():
    repos = [r for r in (ROOT / "bench" / "repos.txt").read_text().split() if r]
    assert REPORT["repos"] == len(repos)
    assert REPORT["read"] == len(REPORT["results"])
    assert REPORT["facts_learned_then"] == sum(r["learned"] for r in REPORT["results"])
    assert REPORT["facts_changed"] == sum(len(r["changed"]) for r in REPORT["results"])
    assert REPORT["with_changes"] == sum(1 for r in REPORT["results"] if r["changed"])
    for r in REPORT["results"]:
        assert r["blocked"] == bool(r["changed"])
        assert not r["fixed_blocked"]
        for c in r["changed"]:
            assert c["old"] != c["new"]
            assert c["old_url"].startswith(
                f"https://github.com/{r['repo']}/blob/{r['old']['sha']}/"
            )


def test_store_holds_every_fact_and_supersession():
    with open_vault(VAULT) as conn:
        facts = rows(conn, "MATCH (f:Fact) RETURN count(f) AS n")[0]["n"]
        superseded = rows(conn, "MATCH (:Fact)-[:SUPERSEDES]->(o:Fact) RETURN count(o) AS n")
    assert facts == REPORT["facts_in_memory"]
    assert superseded[0]["n"] == REPORT["superseded_in_vault"] == REPORT["facts_changed"]


def test_fixed_store_passes_review():
    assert not review_vaults(VAULT, VAULT, default_rules()).blocked


def test_readme_and_page_quote_the_report():
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "site" / "index.html").read_text()
    changed, learned = REPORT["facts_changed"], REPORT["facts_learned_then"]
    assert f"{changed} of the {learned} facts" in readme
    assert f"| Repositories read | {REPORT['read']} |" in readme
    assert f">{changed}</b><strong>Of {learned} facts" in page
    assert f"in {REPORT['with_changes']} of the {REPORT['read']} repositories" in page

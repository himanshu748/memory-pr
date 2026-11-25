"""Keyword search plus the facts a match reaches along DERIVED_FROM."""

from __future__ import annotations

from pathlib import Path

from mempr.search import LEXICAL, NEIGHBORHOOD, format_search, search

ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory"


def test_postgres_also_returns_facts_that_do_not_say_postgres():
    result = search(MEMORY, "postgres")
    values = {hit["value"] for hit in result["hits"]}
    assert values == {"postgres-15"}
    connected = {row["fact"]: row for row in result["connected"]}
    assert connected["payments-api--backup-job--nightly-pg-dump"]["via"] == "follows"
    assert connected["payments-api--backup-job--nightly-pg-dump"]["hops"] == 1
    # The restore drill is two hops away and its text does not contain "postgres".
    restore = connected["payments-api--restore-drill--quarterly-pg-restore"]
    assert restore["via"] == "follows" and restore["hops"] == 2
    assert "postgres" not in restore["value"]
    text = format_search(result)
    assert "match" in text and "follows" in text


def test_a_derived_fact_search_walks_back_to_what_supports_it():
    result = search(MEMORY, "pg-dump")
    assert [hit["fact"] for hit in result["hits"]] == ["payments-api--backup-job--nightly-pg-dump"]
    support = next(row for row in result["connected"] if row["via"] == "supports")
    assert support["fact"] == "payments-api--database--postgres-15"
    assert support["hops"] == 1


def test_a_source_path_is_a_match():
    result = search(MEMORY, "service.yaml")
    assert result["hits"]
    assert all(
        any("service.yaml" in (uri or "") for uri in hit["sources"]) for hit in result["hits"]
    )


def test_a_short_query_does_not_scan():
    result = search(MEMORY, " p ")
    assert result["hits"] == [] and result["connected"] == []
    assert "at least" in result["note"]
    assert "at least" in format_search(result)


def test_the_page_runs_the_same_cypher():
    page = (ROOT / "site" / "search.js").read_text()
    for query in (LEXICAL, NEIGHBORHOOD):
        assert " ".join(query.split()) in " ".join(page.split())

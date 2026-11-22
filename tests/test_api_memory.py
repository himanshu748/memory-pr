"""GET /api/recall and POST /api/query, without Vercel: the handlers run on a local port."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from test_api_review import load

from mempr import agentapi
from mempr.agentapi import MAX_QUERY, MAX_ROWS, BadRequest, recall_entity, run_query

ROOT = Path(__file__).resolve().parent.parent
MEMORY = ROOT / "memory"


def serve(name: str):
    httpd = HTTPServer(("127.0.0.1", 0), load(name).handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture(scope="module")
def recall_url():
    httpd = serve("recall")
    yield f"http://127.0.0.1:{httpd.server_port}/api/recall"
    httpd.shutdown()


@pytest.fixture(scope="module")
def query_url():
    httpd = serve("query")
    yield f"http://127.0.0.1:{httpd.server_port}/api/query"
    httpd.shutdown()


def fetch(url: str, body=None, method: str | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_recall_gives_the_current_facts_with_their_sources(recall_url):
    status, body = fetch(recall_url + "?entity=payments-api")
    assert status == 200
    assert body["entity"] == "payments-api" and body["known"] and not body["truncated"]
    assert body["fact_count"] == len(body["facts"]) > 0
    database = next(f for f in body["facts"] if f["predicate"] == "database")
    assert database["value"] == "postgres-15"
    assert database["sources"] and database["agents"]


def test_recall_of_an_unknown_entity_is_an_empty_answer_not_an_error(recall_url):
    status, body = fetch(recall_url + "?entity=no-such-thing")
    assert (status, body["known"], body["facts"]) == (200, False, [])


@pytest.mark.parametrize("query", ["", "?entity=", "?entity=" + "x" * 81, "?entity=a%0Ab"])
def test_recall_refuses_a_missing_or_overlong_entity(recall_url, query):
    status, body = fetch(recall_url + query)
    assert status in (400, 422) and "error" in body


def test_recall_treats_the_entity_as_data(recall_url):
    status, body = fetch(recall_url + "?entity=x%27%7D)%20DETACH%20DELETE%20e%20//")
    assert (status, body["known"]) == (200, False)
    assert recall_entity(MEMORY, "payments-api")["known"]


def test_recall_does_not_accept_post(recall_url):
    assert fetch(recall_url, {"entity": "payments-api"})[0] == 405


def test_query_returns_rows_and_takes_parameters(query_url):
    status, body = fetch(
        query_url,
        {
            "cypher": "MATCH (f:Fact)-[:ABOUT]->(e:Entity {name: $entity}) "
            "WHERE f.predicate = $p RETURN f.value AS value",
            "params": {"entity": "payments-api", "p": "database"},
        },
    )
    assert status == 200
    assert body == {"rows": [{"value": "postgres-15"}], "row_count": 1, "truncated": False}


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (f:Fact) DETACH DELETE f",
        "match (f:Fact) set f.value = 'x' return f",
        "CREATE (:Entity {name: 'x'})",
        "MERGE (:Entity {name: 'x'})",
        "MATCH (f:Fact) REMOVE f.value",
    ],
)
def test_query_refuses_writes_and_the_memory_is_untouched(query_url, cypher):
    before = files(MEMORY)
    status, body = fetch(query_url, {"cypher": cypher})
    assert status == 403 and "read-only" in body["error"]
    assert files(MEMORY) == before


def test_query_allows_write_words_inside_text(query_url):
    status, body = fetch(
        query_url, {"cypher": "MATCH (f:Fact) WHERE f.value = 'CREATE SET' RETURN f.name AS n"}
    )
    assert (status, body["rows"]) == (200, [])


def test_query_caps_the_rows(query_url):
    status, body = fetch(query_url, {"cypher": "UNWIND range(1, 1000) AS i RETURN i"})
    assert status == 200
    assert (body["row_count"], body["truncated"]) == (MAX_ROWS, True)
    assert body["rows"][0] == {"i": 1}


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"cypher": ""},
        {"cypher": 7},
        {"cypher": "RETURN 1 AS n //" + "x" * MAX_QUERY},
        {"cypher": "RETURN $a AS a", "params": []},
        {"cypher": "RETURN $a AS a", "params": {"a": {"nested": 1}}},
        {"cypher": "RETURN $a AS a", "params": {"a": [1, 2]}},
    ],
)
def test_query_refuses_a_malformed_request(query_url, body):
    status, answer = fetch(query_url, body)
    assert status == 422 and "error" in answer


def test_query_reports_a_cypher_error_as_json(query_url):
    status, body = fetch(query_url, {"cypher": "MATCH (n RETURN n"})
    assert status == 400 and body["error"].startswith("Relate refused the query")


def test_query_does_not_accept_get(query_url):
    assert fetch(query_url)[0] == 405


def test_a_query_that_runs_too_long_is_stopped():
    slow = {"cypher": "MATCH (a), (b), (c), (d), (e), (f), (g) RETURN count(*) AS n"}
    with pytest.raises(BadRequest) as stopped:
        run_query(MEMORY, slow, time_limit=0.5)
    assert stopped.value.status == 408


def test_a_result_that_is_too_large_is_refused(monkeypatch):
    monkeypatch.setattr(agentapi, "MAX_RESULT", 100)
    with pytest.raises(BadRequest) as refused:
        run_query(MEMORY, {"cypher": "UNWIND range(1, 100) AS i RETURN i"})
    assert refused.value.status == 413


def test_the_query_process_does_not_get_the_servers_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "not-a-real-token")
    seen = {}
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.update(kwargs["env"])
        return real(*args, **kwargs)

    monkeypatch.setattr(agentapi.subprocess, "run", spy)
    assert run_query(MEMORY, {"cypher": "RETURN 1 AS n"})["rows"] == [{"n": 1}]
    assert "GITHUB_DISPATCH_TOKEN" not in seen

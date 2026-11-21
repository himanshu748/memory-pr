"""POST /api/review, without Vercel: the handler is served on a local port."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from mempr.playground import MAX_MERGED, BadDraft, clean, review_request
from mempr.rules import load_rules
from mempr.web import MAX_BODY, cors

ROOT = Path(__file__).resolve().parent.parent
RULES = load_rules(None)
MYSQL = {
    "agent": "visitor",
    "entity": "payments-api",
    "predicate": "database",
    "value": "mysql-8",
    "source": "docs/design.md",
}
PAGES = "https://himanshu748.github.io"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"api_{name}", ROOT / "api" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), load("review").handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def call(
    url: str, body=None, *, raw: bytes | None = None, method="POST", origin: str | None = None
):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/json"} | ({"Origin": origin} if origin else {})
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode()
            return response.status, json.loads(text) if text else None, response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode()), error.headers


def test_a_contradicting_draft_is_blocked_with_the_comment_the_check_posts(server):
    status, body, headers = call(server, {"draft": MYSQL})
    assert status == 200
    assert body["blocked"] is True
    assert body["rules"] == ["contradiction"]
    assert body["comment"].startswith("## Memory review: blocked (1 problem)")
    assert "Fact/payments-api--database--postgres-15" in body["comment"]
    assert body["review"]["findings"][0]["row"]["other_value"] == "postgres-15"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Type"].startswith("application/json")
    assert not (ROOT / "memory" / ".relate").exists()  # the committed memory is left alone


def test_a_merged_draft_becomes_part_of_the_memory_the_next_one_is_reviewed_against(server):
    port = {**MYSQL, "entity": "checkout-web", "predicate": "port", "value": "8080"}
    fixed = {**port, "retract": ["checkout-web--port--3000"]}
    again = {**port, "value": "9090"}
    assert call(server, {"draft": fixed})[1]["blocked"] is False
    status, body, _ = call(server, {"merged": [fixed], "draft": again})
    assert status == 200
    assert body["rules"] == ["contradiction"]
    assert "checkout-web--port--8080" in body["comment"]  # the visitor's own merged fact
    assert "checkout-web--port--3000" not in json.dumps(body["review"]["findings"])


@pytest.mark.parametrize(
    "draft, word",
    [
        ({**MYSQL, "entity": "Payments API"}, "entity"),
        ({**MYSQL, "agent": "a" * 81}, "agent"),
        ({**MYSQL, "value": ""}, "value"),
        ({**MYSQL, "value": "x\ny"}, "value"),
        ({**MYSQL, "value": 5}, "value"),
        ({**MYSQL, "supersedes": ["'}) DETACH DELETE n //"]}, "supersedes"),
        ({**MYSQL, "supersedes": "payments-api--database--postgres-15"}, "supersedes"),
        ({**MYSQL, "retract": ["x"] * 13}, "retract"),
        ({**MYSQL, "supersedes": ["no-such--fact--here"]}, "no fact named"),
        ({"kind": "retract", "target": "no-such--fact--here"}, "no fact named"),
        ({"kind": "delete-everything"}, "kind"),
        ("mysql", "object"),
    ],
)
def test_a_draft_that_is_not_well_formed_is_refused(server, draft, word):
    status, body, _ = call(server, {"draft": draft})
    assert status == 422
    assert word in body["error"]
    assert set(body) == {"error"}


def test_text_from_the_visitor_is_data_not_cypher(server):
    value = "x'}) MATCH (n) DETACH DELETE n //"
    status, body, _ = call(server, {"draft": {**MYSQL, "predicate": "motto", "value": value}})
    assert status == 200
    assert body["blocked"] is False
    props = [t for t in body["review"]["added"] if t[0] == "prop" and t[2] == "value"]
    assert json.loads(props[0][3]) == value
    assert body["review"]["removed"] == []


def test_requests_that_are_not_json_or_are_too_large_are_refused(server):
    assert call(server, raw=b"{not json")[0] == 400
    assert call(server, raw=b"")[0] == 400
    assert call(server, raw=b" " * (MAX_BODY + 1))[0] == 413
    assert call(server, {"merged": [MYSQL] * (MAX_MERGED + 1), "draft": MYSQL})[0] == 422
    assert call(server, [1, 2])[0] == 422
    status, body, _ = call(server, method="GET")
    assert (status, body) == (405, {"error": "method not allowed"})


def test_only_the_pages_mirror_may_call_from_another_origin(server):
    assert cors(PAGES)["Access-Control-Allow-Origin"] == PAGES
    assert "Access-Control-Allow-Origin" not in cors("https://example.com")
    assert "Access-Control-Allow-Origin" not in cors(None)
    status, _, headers = call(server, method="OPTIONS", origin=PAGES)
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == PAGES
    assert call(server, {"draft": MYSQL}, origin=PAGES)[2]["Access-Control-Allow-Origin"] == PAGES
    other = call(server, {"draft": MYSQL}, origin="https://example.com")[2]
    assert other["Access-Control-Allow-Origin"] is None


def test_clean_keeps_only_the_fields_the_review_uses():
    draft = clean({**MYSQL, "kind": "fact", "html": "<img onerror=alert(1)>", "source": " "})
    assert "html" not in draft
    assert draft["source"] == ""
    with pytest.raises(BadDraft):
        review_request({"draft": MYSQL, "merged": "all"}, ROOT / "memory", RULES)

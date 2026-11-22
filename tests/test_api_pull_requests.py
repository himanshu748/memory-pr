"""The real pull request from the page, without Vercel or GitHub: GitHub is a stand-in."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

import pytest
import yaml

from mempr import playground_pr
from mempr.playground import BadDraft
from mempr.playground_pr import Refused, from_inputs, open_request, state, strict
from mempr.rules import load_rules

ROOT = Path(__file__).resolve().parent.parent
MEMORY = ROOT / "memory"
RULES = load_rules(None)
ON = {"GITHUB_DISPATCH_TOKEN": "not-a-real-token"}
MYSQL = {
    "agent": "visitor",
    "entity": "payments-api",
    "predicate": "database",
    "value": "mysql-8.4",
    "source": "docs/design.md",
}
ID = "0123456789"
PULL_URL = "https://github.com/himanshu748/memory-pr/pull/41"


class GitHub:
    """Answers by path prefix and remembers every call."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, token=None, body=None):
        self.calls.append((method, path, token, body))
        for key, answer in self.answers.items():
            if key in path:
                return answer
        return 404, None


def pull(n: int, ref: str, state: str = "open") -> dict:
    return {
        "number": n,
        "state": state,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "html_url": PULL_URL,
        "head": {"ref": ref, "sha": "abc"},
    }


def test_inputs_are_short_lowercase_words_and_survive_the_round_trip():
    inputs = strict(MYSQL | {"supersedes": ["payments-api--database--postgres-15"]})
    assert inputs == MYSQL | {"supersedes": "payments-api--database--postgres-15", "retract": ""}
    assert all(isinstance(v, str) for v in inputs.values())
    draft = from_inputs(inputs)
    assert draft["supersedes"] == ["payments-api--database--postgres-15"]
    assert draft["value"] == "mysql-8.4"


@pytest.mark.parametrize(
    "change",
    [
        {"value": "MySQL 8"},
        {"value": "$(curl evil.example)"},
        {"value": "a;b"},
        {"value": "`id`"},
        {"value": "${{ secrets.X }}"},
        {"value": "a\nb"},
        {"value": "-rf"},
        {"value": "x" * 61},
        {"entity": "Payments"},
        {"entity": "a b"},
        {"predicate": "uses_db"},
        {"agent": "visitor'"},
        {"source": "../secrets"},
        {"source": "/etc/passwd"},
        {"source": "docs/Design.md"},
        {"source": ""},
        {"source": "https://example.com"},
        {"kind": "retract", "target": "payments-api--database--postgres-15"},
        {"supersedes": ["a,b"]},
        {"supersedes": ["x"] * 5},
    ],
)
def test_anything_else_is_refused_by_the_server_and_again_by_the_workflow(change):
    with pytest.raises(BadDraft):
        strict(MYSQL | change)
    github = GitHub()
    with pytest.raises(BadDraft):
        open_request({"draft": MYSQL | change}, MEMORY, RULES, ON, github)
    assert github.calls == []
    if "kind" not in change and not isinstance(change.get("supersedes"), list):
        with pytest.raises(BadDraft):
            from_inputs({"supersedes": "", "retract": ""} | MYSQL | change)


def test_inputs_not_in_the_servers_form_are_refused_by_the_workflow():
    with pytest.raises(BadDraft):
        from_inputs(MYSQL | {"supersedes": "a,,b", "retract": ""})
    with pytest.raises(BadDraft):
        from_inputs(MYSQL | {"supersedes": " a", "retract": ""})


def test_without_the_token_it_says_not_enabled_and_calls_nothing():
    github = GitHub()
    assert playground_pr.enabled({})["enabled"] is False
    assert playground_pr.enabled(ON)["enabled"] is True
    with pytest.raises(Refused) as refused:
        open_request({"draft": MYSQL}, MEMORY, RULES, {}, github)
    assert refused.value.status == 503 and "not enabled" in refused.value.message
    assert github.calls == []


def test_a_good_draft_starts_the_workflow_with_the_token_and_only_strings():
    github = GitHub(**{"/pulls": (200, []), "/dispatches": (204, None)})
    answer = open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)
    assert re.fullmatch(r"[a-f0-9]{10}", answer["id"])
    assert answer["branch"] == f"playground/{answer['id']}" and answer["state"] == "queued"
    assert answer["expected"] == {"blocked": True, "rules": ["contradiction"]}
    assert "not-a-real-token" not in json.dumps(answer)
    (_, _, no_token, _), _, (method, path, token, body) = github.calls
    assert no_token is None  # pull requests are read with no token
    assert method == "POST" and path.endswith("/actions/workflows/playground-agent.yml/dispatches")
    assert token == "not-a-real-token"
    assert body == {
        "ref": "main",
        "inputs": {"id": answer["id"], **MYSQL, "supersedes": "", "retract": ""},
    }


def test_three_open_playground_pull_requests_is_the_limit():
    three = [pull(n, f"playground/{n:010x}") for n in range(3)] + [pull(9, "devin/other")]
    github = GitHub(**{"/pulls": (200, three)})
    with pytest.raises(Refused) as refused:
        open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)
    assert refused.value.status == 429
    assert all(method == "GET" for method, *_ in github.calls)
    two = GitHub(**{"/pulls": (200, three[1:]), "/dispatches": (204, None)})
    assert open_request({"draft": MYSQL}, MEMORY, RULES, ON, two)["state"] == "queued"


def test_open_pull_requests_past_their_30_minutes_take_no_place():
    # The cleanup schedule can be late. The workflow closes these before it opens the next,
    # so the server must let the request through to it.
    late = (datetime.now(timezone.utc) - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = [pull(n, f"playground/{n:010x}") | {"created_at": late} for n in range(3)]
    github = GitHub(**{"/pulls": (200, stale), "/dispatches": (204, None)})
    assert open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)["state"] == "queued"
    fresh = GitHub(
        **{"/pulls": (200, stale[:1] + [pull(n, f"playground/{n:010x}") for n in (5, 6, 7)])}
    )
    with pytest.raises(Refused) as refused:
        open_request({"draft": MYSQL}, MEMORY, RULES, ON, fresh)
    assert refused.value.status == 429


def test_twenty_in_a_day_is_the_limit_whatever_their_state():
    closed = [pull(n, f"playground/{n:010x}", "closed") for n in range(20)]
    github = GitHub(**{"/pulls": (200, closed)})
    with pytest.raises(Refused) as refused:
        open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)
    assert refused.value.status == 429 and "24 hours" in refused.value.message
    assert all(method == "GET" for method, *_ in github.calls)
    old = closed[:19] + [closed[19] | {"created_at": "2020-01-01T00:00:00Z"}]
    fewer = GitHub(**{"/pulls": (200, old), "/dispatches": (204, None)})
    assert open_request({"draft": MYSQL}, MEMORY, RULES, ON, fewer)["state"] == "queued"


def test_the_workflow_counts_the_same_way():
    agent = load("../demo/playground_agent.py")
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)

    def made(n: int, state: str, hours: float) -> dict:
        at = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"number": n, "headRefName": f"playground/{n}", "state": state, "createdAt": at}

    assert agent.refusal([made(n, "CLOSED", 1) for n in range(19)], now) is None
    assert "24 hours" in agent.refusal([made(n, "CLOSED", 23) for n in range(20)], now)
    assert agent.refusal([made(n, "MERGED", 25) for n in range(40)], now) is None
    assert "already open" in agent.refusal([made(n, "OPEN", 0.1) for n in range(3)], now)
    assert agent.refusal([made(n, "OPEN", 0.1) for n in range(2)], now) is None


def test_one_pull_request_is_opened_at_a_time():
    # GitHub cancels a waiting run when another joins the group, so a second is not sent.
    for status in ("queued", "in_progress", "pending"):
        runs = {"workflow_runs": [{"status": "completed"}, {"status": status}]}
        github = GitHub(**{"/pulls": (200, []), "/runs": (200, runs)})
        with pytest.raises(Refused) as refused:
            open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)
        assert refused.value.status == 429 and "right now" in refused.value.message
        assert all(method == "GET" for method, *_ in github.calls)
    done = {"workflow_runs": [{"status": "completed"}]}
    github = GitHub(**{"/pulls": (200, []), "/runs": (200, done), "/dispatches": (204, None)})
    assert open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)["state"] == "queued"


def test_when_github_will_not_count_the_workflow_still_decides():
    github = GitHub(**{"/pulls": (403, None), "/dispatches": (204, None)})
    assert open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)["state"] == "queued"


def test_github_refusing_the_dispatch_is_a_502_without_details():
    github = GitHub(**{"/pulls": (200, []), "/dispatches": (401, {"message": "Bad credentials"})})
    with pytest.raises(Refused) as refused:
        open_request({"draft": MYSQL}, MEMORY, RULES, ON, github)
    assert refused.value.status == 502 and "credentials" not in refused.value.message


def test_a_draft_that_changes_nothing_is_not_sent():
    same = MYSQL | {"value": "postgres-15", "agent": "scout"}
    github = GitHub()
    with pytest.raises(BadDraft):
        open_request({"draft": same | {"supersedes": ["no-such-fact"]}}, MEMORY, RULES, ON, github)
    assert github.calls == []


@pytest.mark.parametrize("ident", ["", "nope", "0123456789a", "ABCDEF0123", "../../etc", None, 5])
def test_an_id_the_server_never_gave_is_a_404_and_calls_nothing(ident):
    github = GitHub()
    with pytest.raises(Refused) as refused:
        state(ident, ON, github)
    assert refused.value.status == 404 and github.calls == []


def test_state_before_the_pull_request_exists():
    runs = {
        "workflow_runs": [
            {
                "name": "playground-agent",
                "display_title": f"playground {ID}",
                "status": "in_progress",
            }
        ]
    }
    github = GitHub(**{"/pulls": (200, []), "/runs": (200, runs)})
    assert state(ID, ON, github)["state"] == "queued"
    assert state(ID, {}, GitHub(**{"/pulls": (200, [])}))["state"] == "queued"  # a preview


def test_state_when_the_workflow_refused():
    run = {
        "name": "playground-agent",
        "display_title": f"playground {ID}",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/himanshu748/memory-pr/actions/runs/1",
    }
    github = GitHub(**{"/pulls": (200, []), "/runs": (200, {"workflow_runs": [run]})})
    answer = state(ID, ON, github)
    assert answer["state"] == "failed" and answer["run_url"].endswith("/runs/1")


@pytest.mark.parametrize(
    ("statuses", "check"),
    [
        ([], "pending"),
        ([{"context": "ci", "state": "success"}], "pending"),
        ([{"context": "memory-review", "state": "failure", "target_url": "u"}], "failure"),
        ([{"context": "memory-review", "state": "success", "target_url": "u"}], "success"),
    ],
)
def test_state_of_an_open_pull_request_and_its_check(statuses, check):
    github = GitHub(
        **{
            "/pulls": (200, [pull(41, f"playground/{ID}")]),
            "/status": (200, {"statuses": statuses}),
        }
    )
    answer = state(ID, ON, github)
    assert (answer["state"], answer["number"], answer["url"]) == ("open", 41, PULL_URL)
    assert answer["check"] == check
    assert all(token is None for _, path, token, _ in github.calls if "/actions/" not in path)


def test_githubs_rate_limit_is_an_answer_not_an_error():
    answer = state(ID, ON, GitHub(**{"/pulls": (403, None)}))
    assert answer["state"] == "unknown" and ID in answer["find_url"]
    half = GitHub(**{"/pulls": (200, [pull(41, f"playground/{ID}")]), "/status": (403, None)})
    assert state(ID, ON, half)["check"] == "unknown"
    assert state(ID, ON, GitHub(**{"/pulls": (0, None)}))["state"] == "unknown"


def test_the_workflow_writes_the_same_note_mempr_would(tmp_path):
    vault = tmp_path / "memory"
    shutil.copytree(MEMORY, vault)
    before = {p.name for p in vault.rglob("*.md")}
    playground_pr.write(vault, strict(MYSQL))
    new = {p.name for p in vault.rglob("*.md")} - before
    assert "payments-api--database--mysql-8-4.md" in new
    with pytest.raises(BadDraft):
        playground_pr.write(vault, strict(MYSQL) | {"value": "$(id)"})


# The endpoint itself


def load(name: str):
    spec = importlib.util.spec_from_file_location("api_pull_requests", ROOT / "api" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def url(monkeypatch):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    module = load("pull-requests.py")
    httpd = HTTPServer(("127.0.0.1", 0), module.handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/api/pull-requests"
    httpd.shutdown()


def fetch(url: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_a_deployment_without_the_token_says_so(url):
    assert fetch(url) == (200, {"enabled": False, "limit": 3, "daily": 20, "minutes": 30})
    status, body = fetch(url, {"draft": MYSQL})
    assert status == 503 and "not enabled" in body["error"]


def test_the_endpoint_refuses_bad_drafts_and_bad_ids_as_json(url, monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "not-a-real-token")
    assert fetch(url)[1]["enabled"] is True
    status, body = fetch(url, {"draft": MYSQL | {"value": "MySQL 8"}})
    assert status == 422 and "value" in body["error"]
    assert fetch(url, [1])[0] == 422
    assert fetch(url + "/not-an-id")[0] == 404
    assert fetch(url + "?id=not-an-id")[0] == 404


def test_the_endpoint_reads_the_id_from_the_path_or_the_rewrite(url, monkeypatch):
    monkeypatch.setattr(playground_pr, "_github", GitHub(**{"/pulls": (403, None)}))
    module = load("pull-requests.py")
    assert module.ident(f"/api/pull-requests/{ID}", {}) == ID
    assert module.ident("/api/pull-requests", {"id": ID}) == ID
    assert module.ident("/api/pull-requests", {}) is None
    assert module.ident("/api/pull-requests/", {}) is None


# The workflows


def workflow(name: str) -> tuple[str, dict]:
    text = (ROOT / ".github" / "workflows" / name).read_text()
    return text, yaml.safe_load(text)


def test_no_input_is_ever_part_of_a_command():
    for name in ("playground-agent.yml", "playground-cleanup.yml"):
        text, parsed = workflow(name)
        for job in parsed["jobs"].values():
            for step in job["steps"]:
                assert "${{" not in step.get("run", ""), step
        assert parsed["concurrency"]["cancel-in-progress"] is False
    text, parsed = workflow("playground-agent.yml")
    inputs = set(parsed[True]["workflow_dispatch"]["inputs"])  # yaml reads `on` as True
    assert inputs == {"id", *strict(MYSQL)}
    env = parsed["jobs"]["open"]["env"]
    assert {k for k in env if k.startswith("PLAYGROUND_")} == {
        f"PLAYGROUND_{k.upper()}" for k in inputs
    }
    assert parsed["run-name"] == "playground ${{ inputs.id }}"  # how the server finds the run


def test_cleanup_closes_only_what_is_older_than_half_an_hour():
    agent = load("../demo/playground_agent.py")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def at(minutes: int) -> str:
        return (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    pulls = [
        {"number": 1, "createdAt": at(31), "state": "OPEN"},
        {"number": 2, "createdAt": at(29), "state": "OPEN"},
        {"number": 3, "createdAt": at(90), "state": "CLOSED"},
    ]
    assert [p["number"] for p in agent.expired(pulls, now)] == [1]


def test_the_workflow_script_refuses_before_it_touches_git(monkeypatch):
    agent = load("../demo/playground_agent.py")
    ran = []
    monkeypatch.setattr(agent, "sh", lambda *args: ran.append(args) or "[]")
    env = {f"PLAYGROUND_{k.upper()}": v for k, v in strict(MYSQL).items()}
    for bad in (
        env | {"PLAYGROUND_ID": "../x"},
        env | {"PLAYGROUND_ID": ID, "PLAYGROUND_VALUE": "$(id)"},
        env | {"PLAYGROUND_ID": ID, "PLAYGROUND_SOURCE": "../x"},
        {"PLAYGROUND_ID": ID},
    ):
        with pytest.raises(SystemExit) as stopped:
            agent.open_pull(bad)
        assert str(stopped.value).startswith("refused")
    assert ran == []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    full = json.dumps(
        [
            {
                "number": n,
                "headRefName": f"playground/{n}",
                "isCrossRepository": False,
                "createdAt": now,
                "state": "OPEN",
            }
            for n in range(3)
        ]
    )
    monkeypatch.setattr(agent, "sh", lambda *args: ran.append(args) or full)
    with pytest.raises(SystemExit) as stopped:
        agent.open_pull(env | {"PLAYGROUND_ID": ID})
    assert "already open" in str(stopped.value)
    # It cleans up and counts, and nothing else: no branch, no push.
    assert {args[:3] for args in ran} == {("gh", "pr", "list")}


def test_expired_pull_requests_give_up_their_places_before_the_count(monkeypatch):
    agent = load("../demo/playground_agent.py")
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pulls = [
        {
            "number": n,
            "headRefName": f"playground/{n}",
            "isCrossRepository": False,
            "createdAt": old,
            "state": "OPEN",
        }
        for n in range(3)
    ]
    ran = []

    def sh(*args):
        ran.append(args)
        if args[:3] == ("gh", "pr", "close"):
            next(p for p in pulls if str(p["number"]) == args[3])["state"] = "CLOSED"
        if args[:2] == ("git", "checkout"):
            raise SystemExit("stop here")
        return json.dumps(pulls)

    monkeypatch.setattr(agent, "sh", sh)
    env = {f"PLAYGROUND_{k.upper()}": v for k, v in strict(MYSQL).items()}
    with pytest.raises(SystemExit) as stopped:
        agent.open_pull(env | {"PLAYGROUND_ID": ID})
    assert str(stopped.value) == "stop here"  # it got past the count
    assert [args[3] for args in ran if args[:3] == ("gh", "pr", "close")] == ["0", "1", "2"]


def test_other_methods_get_a_json_405(url):
    for method in ("DELETE", "PUT", "PATCH"):
        request = urllib.request.Request(url, method=method, data=b"{}")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=10)
        assert refused.value.code == 405
        assert json.loads(refused.value.read()) == {"error": "method not allowed"}


def test_vercel_builds_nothing_for_playground_branches():
    config = json.loads((ROOT / "vercel.json").read_text())
    assert config["git"]["deploymentEnabled"] == {"playground/*": False}

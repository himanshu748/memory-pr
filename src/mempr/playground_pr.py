"""Open a real pull request from a page draft, without the server writing to the repository.

The server checks the draft and asks GitHub to run `.github/workflows/playground-agent.yml`.
That workflow checks it again, writes the note with `mempr` and opens the pull request.
The server's token can only start and read workflow runs; pull request state is read from
GitHub's public API with no token.
"""

from __future__ import annotations

import json
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .playground import BadDraft, clean, review_request
from .rules import Rule

REPO = "himanshu748/memory-pr"
WORKFLOW = "playground-agent.yml"
BRANCH_PREFIX = "playground/"
MAX_OPEN = 3
TOKEN_ENV = "GITHUB_DISPATCH_TOKEN"
API = "https://api.github.com"
ID = re.compile(r"[a-f0-9]{10}")
WORD = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")  # letters, digits, "." and "-"
SOURCE = re.compile(rf"{WORD.pattern}(?:/{WORD.pattern})*")  # words joined by "/"
MAX_WORD = 60
MAX_SOURCE = 100
MAX_LINKS = 4  # facts one pull request may replace or retract


class Refused(Exception):
    """The request cannot go ahead. `status` is the HTTP status to give."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _word(draft: dict, key: str) -> str:
    text = draft.get(key)
    if not isinstance(text, str) or len(text) > MAX_WORD or not WORD.fullmatch(text):
        raise BadDraft(
            f"{key} must be lowercase letters, digits, dots and hyphens, {MAX_WORD} at most, "
            "for a pull request on GitHub"
        )
    return text


def strict(draft: Any) -> dict[str, str]:
    """A draft as the workflow's inputs: short lowercase words only. Raises BadDraft.

    The server and the workflow both call this, so neither trusts the other.
    """
    draft = clean(draft)
    if draft["kind"] != "fact":
        raise BadDraft("only a new fact can be opened as a pull request on GitHub")
    source = draft["source"]
    if len(source) > MAX_SOURCE or not SOURCE.fullmatch(source):
        raise BadDraft(
            "source must be a path of lowercase letters, digits, dots and hyphens, "
            f"{MAX_SOURCE} at most, for a pull request on GitHub"
        )
    for key in ("supersedes", "retract"):
        if len(draft[key]) > MAX_LINKS:
            raise BadDraft(f"{key} may name {MAX_LINKS} facts at most on GitHub")
    return {
        "agent": _word(draft, "agent"),
        "entity": _word(draft, "entity"),
        "predicate": _word(draft, "predicate"),
        "value": _word(draft, "value"),
        "source": source,
        "supersedes": ",".join(draft["supersedes"]),
        "retract": ",".join(draft["retract"]),
    }


def from_inputs(inputs: dict[str, str]) -> dict[str, Any]:
    """The draft a set of workflow inputs stands for, checked again. Raises BadDraft."""
    draft: dict[str, Any] = {k: inputs.get(k, "") for k in ("agent", "entity", "predicate")}
    draft |= {"value": inputs.get("value", ""), "source": inputs.get("source", "")}
    for key in ("supersedes", "retract"):
        draft[key] = [name for name in inputs.get(key, "").split(",") if name]
    if strict(draft) != {k: inputs.get(k, "") for k in strict(draft)}:
        raise BadDraft("the inputs are not in the form the server sends")
    return clean(draft)


def check_id(value: Any) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Refused(404, "no such playground pull request")
    return value


def title(draft: dict[str, Any]) -> str:
    return f"memory: {draft['entity']} {draft['predicate']} {draft['value']} (playground)"


def _github(method: str, path: str, token: str | None = None, body: Any = None) -> tuple[int, Any]:
    """One call to GitHub's API: the status and the JSON it gave (None when it gave none)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "memory-pr-playground",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, b""
    except (urllib.error.URLError, TimeoutError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


def open_count(github=_github) -> int | None:
    """How many playground pull requests are open, or None when GitHub would not say."""
    status, pulls = github("GET", f"/repos/{REPO}/pulls?state=open&per_page=100")
    if status != 200 or not isinstance(pulls, list):
        return None
    return sum(1 for p in pulls if str(p.get("head", {}).get("ref", "")).startswith(BRANCH_PREFIX))


def enabled(env: dict[str, str]) -> dict[str, Any]:
    return {"enabled": bool(env.get(TOKEN_ENV)), "limit": MAX_OPEN, "minutes": 30}


def open_request(
    body: Any, vault: Path, rules: list[Rule], env: dict[str, str], github=_github
) -> dict[str, Any]:
    """Check a draft and ask the workflow to open it as a pull request.

    Raises BadDraft for a draft that is not acceptable, Refused when it cannot go ahead.
    """
    token = env.get(TOKEN_ENV)
    if not token:
        raise Refused(503, "opening pull requests on GitHub is not enabled on this deployment")
    if not isinstance(body, dict):
        raise BadDraft("the body must be a JSON object with a draft")
    inputs = strict(body.get("draft"))
    review = review_request({"draft": body.get("draft")}, vault, rules)  # it must apply to main
    if not review["review"].get("added"):
        raise BadDraft("this draft changes nothing in the committed memory")
    count = open_count(github)
    if count is not None and count >= MAX_OPEN:
        raise Refused(
            429,
            f"{MAX_OPEN} playground pull requests are already open. "
            "They close after 30 minutes; try again then.",
        )
    if _waiting(token, github):
        # GitHub keeps one waiting run per concurrency group and cancels the one before it.
        raise Refused(429, "Another pull request is being opened right now. Try again in a minute.")
    ident = secrets.token_hex(5)
    status, _ = github(
        "POST",
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        token,
        {"ref": "main", "inputs": {"id": ident, **inputs}},
    )
    if status != 204:
        raise Refused(502, "GitHub did not accept the request. Try again in a minute.")
    return {
        "id": ident,
        "branch": BRANCH_PREFIX + ident,
        "state": "queued",
        "expected": {"blocked": review["blocked"], "rules": review["rules"]},
    }


def _waiting(token: str, github) -> bool:
    """True when a run of the workflow has not finished. False when GitHub would not say."""
    status, found = github(
        "GET", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=10", token
    )
    if status != 200 or not isinstance(found, dict):
        return False
    return any(run.get("status") != "completed" for run in found.get("workflow_runs", []))


def _run(ident: str, token: str | None, github) -> dict[str, Any] | None:
    """The workflow run for an id, found by its name. Needs the token."""
    if not token:
        return None
    status, found = github(
        "GET", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=30", token
    )
    if status != 200 or not isinstance(found, dict):
        return None
    for run in found.get("workflow_runs", []):
        # GitHub gives the workflow's `run-name` as the run's `display_title`.
        if run.get("display_title") == f"playground {ident}":
            return run
    return None


def state(ident: Any, env: dict[str, str], github=_github) -> dict[str, Any]:
    """Where a playground pull request stands: queued, open with its check, closed or failed."""
    ident = check_id(ident)
    branch = BRANCH_PREFIX + ident
    owner = REPO.split("/")[0]
    answer: dict[str, Any] = {
        "id": ident,
        "branch": branch,
        "find_url": f"https://github.com/{REPO}/pulls?q=is%3Apr+head%3A{branch}",
    }
    status, pulls = github("GET", f"/repos/{REPO}/pulls?state=all&head={owner}:{branch}")
    if status != 200 or not isinstance(pulls, list):
        # GitHub's limit for callers with no token, or GitHub is unreachable.
        return answer | {
            "state": "unknown",
            "note": "GitHub is not answering this server right now. Use the link to look.",
        }
    if not pulls:
        run = _run(ident, env.get(TOKEN_ENV), github)
        if run and run.get("status") == "completed" and run.get("conclusion") != "success":
            return answer | {
                "state": "failed",
                "run_url": run.get("html_url"),
                "note": "The workflow did not open a pull request. "
                f"It refuses when {MAX_OPEN} are already open.",
            }
        return answer | {"state": "queued", "run_url": run.get("html_url") if run else None}
    pull = pulls[0]
    answer |= {
        "state": "open" if pull.get("state") == "open" else "closed",
        "number": pull.get("number"),
        "url": pull.get("html_url"),
        "check": "pending",
    }
    sha = pull.get("head", {}).get("sha")
    status, combined = github("GET", f"/repos/{REPO}/commits/{sha}/status") if sha else (0, None)
    if status != 200 or not isinstance(combined, dict):
        return answer | {"check": "unknown"}
    for entry in combined.get("statuses", []):
        if entry.get("context") == "memory-review":
            # success, failure, error or pending
            return answer | {"check": entry.get("state"), "check_url": entry.get("target_url")}
    return answer


def write(vault: Path, inputs: dict[str, str]) -> dict[str, Any]:
    """Run by the workflow: check the inputs again and write the change into the checkout."""
    from .playground import apply

    draft = from_inputs(inputs)
    apply(vault, draft)
    return draft

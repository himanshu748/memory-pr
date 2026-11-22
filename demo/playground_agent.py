"""Run by GitHub Actions: open a visitor's draft from the page as a real pull request,
and close the ones that are older than half an hour.

The inputs arrive as environment variables and are checked again here, with the same code
the server used, before anything is written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mempr import playground_pr
from mempr.playground import BadDraft

ROOT = Path(__file__).resolve().parent.parent
LABEL = "playground"
LABEL_TEXT = "Opened from the page's Write a memory form; closed after 30 minutes"
CLOSED = "Closed automatically: playground pull requests are kept for 30 minutes."
KEEP = playground_pr.KEEP
WAIT = int(KEEP.total_seconds() // 60) + 1  # minutes until the cleanup a new one books
INPUTS = ("agent", "entity", "predicate", "value", "source", "supersedes", "retract")
BOT = (
    "user.name=github-actions[bot]",
    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
)


def sh(*args: str) -> str:
    done = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"$ {' '.join(args)}\n{done.stdout}{done.stderr}")
    return done.stdout.strip()


def playground_pulls() -> list[dict]:
    """This repository's playground pull requests, open or not, newest first."""
    found = json.loads(
        sh("gh", "pr", "list", "--state", "all", "--limit", "200", "--json",
           "number,headRefName,createdAt,isCrossRepository,state")
    )  # fmt: skip
    return [
        p
        for p in found
        if p["headRefName"].startswith(playground_pr.BRANCH_PREFIX) and not p["isCrossRepository"]
    ]


def expired(pulls: list[dict], now: datetime) -> list[dict]:
    """The open ones that are older than half an hour."""
    return [
        p
        for p in pulls
        if p.get("state", "OPEN") == "OPEN"
        and now - datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00")) > KEEP
    ]


def refusal(pulls: list[dict], now: datetime) -> str | None:
    """Why no pull request may be opened now, or None. The server asks the same first."""
    rows = [(p["headRefName"], p["state"] == "OPEN", p["createdAt"]) for p in pulls]
    return playground_pr.full(*playground_pr.count(rows, now))


def open_pull(env: dict[str, str]) -> None:
    ident = env.get("PLAYGROUND_ID", "")
    if not re.fullmatch(playground_pr.ID.pattern, ident):
        sys.exit("refused: the id is not one the server gives")
    inputs = {k: env.get(f"PLAYGROUND_{k.upper()}", "") for k in INPUTS}
    try:
        draft = playground_pr.from_inputs(inputs)
    except BadDraft as error:
        sys.exit(f"refused: {error}")
    # The scheduled cleanup can be late, so expired pull requests give up their places first.
    stuck = cleanup()
    pulls = playground_pulls()
    if any(p["number"] in stuck and p["state"] == "OPEN" for p in pulls):
        # It takes no place in the count, so opening now could leave more than three open.
        sys.exit("refused: an expired playground pull request could not be closed")
    reason = refusal(pulls, datetime.now(timezone.utc))
    if reason:
        sys.exit(f"refused: {reason}")
    branch = playground_pr.BRANCH_PREFIX + ident
    sh("git", "checkout", "-q", "-b", branch, "origin/main")
    try:
        playground_pr.write(ROOT / "memory", inputs)
    except BadDraft as error:
        sys.exit(f"refused: {error}")
    sh("git", "add", "memory")
    title = playground_pr.title(draft)
    sh("git", "-c", BOT[0], "-c", BOT[1], "commit", "-q", "-m", title)
    sh("git", "push", "-q", "origin", branch)
    sh("gh", "label", "create", LABEL, "--force", "--color", "C5DEF5", "--description", LABEL_TEXT)
    body = (
        "Opened from the **Write a memory** form on https://memory-pr.vercel.app/ by a visitor, "
        f"as agent `{draft['agent']}`. The memory review comments below and sets the "
        "`memory-review` check.\n\nThis pull request is a demonstration. It is closed and its "
        "branch deleted about 30 minutes after it was opened."
    )
    url = sh("gh", "pr", "create", "--base", "main", "--head", branch, "--title", title,
             "--body", body, "--label", LABEL)  # fmt: skip
    print(url)
    # GitHub holds workflows for approval on pull requests the Actions token opens, so
    # start the review from the base branch; it reports `memory-review` as a status.
    number = url.rstrip("/").rsplit("/", 1)[-1]
    sh("gh", "workflow", "run", "memory-review.yml", "--ref", "main", "-f", f"pr={number}")
    # GitHub can start the cleanup schedule hours late, so each pull request books its own.
    sh("gh", "workflow", "run", "playground-cleanup.yml", "--ref", "main", "-f", f"wait={WAIT}")


def cleanup() -> list[int]:
    """Close the expired ones. Returns the numbers of those it could not close."""
    stuck = []
    for pull in expired(playground_pulls(), datetime.now(timezone.utc)):
        try:
            sh("gh", "pr", "close", str(pull["number"]), "--delete-branch", "--comment", CLOSED)
        except SystemExit as failed:
            # Another cleanup run may have closed it a moment ago. Go on to the rest.
            print(f"could not close #{pull['number']}: {failed}")
            stuck.append(pull["number"])
            continue
        print(f"closed #{pull['number']} ({pull['headRefName']})")
    return stuck


def wait_minutes(env: dict[str, str]) -> int:
    """How long a cleanup run waits first: whole minutes up to 35, anything else is 0."""
    text = env.get("PLAYGROUND_WAIT", "")
    return int(text) if re.fullmatch(r"[0-9]{1,2}", text) and int(text) <= 35 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["open", "cleanup"])
    if parser.parse_args().command == "open":
        open_pull(dict(os.environ))
    else:
        time.sleep(60 * wait_minutes(dict(os.environ)))
        cleanup()


if __name__ == "__main__":
    main()

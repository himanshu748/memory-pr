"""Run the real-repository review over many public GitHub repositories, into one memory.

For each repository, agent `scout` learns its versions from the last commit before
`--months` ago and agent `builder` learns them from today's head; both are read from the
real files on GitHub. Every repository goes into one Relate vault at `--vault` (the fixed
state: each new fact SUPERSEDES the stale one it replaces) and the per-repository numbers
go to `--out`. Then the stale and fixed memories of all of them are reviewed as one change.

  GITHUB_READ_TOKEN=$(gh auth token) python bench/real.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from mempr.check import review_vaults
from mempr.realrepo import RepoError, learn
from mempr.rules import default_rules
from mempr.vault import open_vault, rows

ROOT = Path(__file__).resolve().parent.parent
REPOS = Path(__file__).with_name("repos.txt")
LABELS = {"Agent", "Entity", "Fact", "Source"}


def _files(graph: dict, root: Path, keep: str | None = None) -> None:
    for n in graph["nodes"]:
        if "file" in n and (keep is None or n["label"] != "Fact" or keep in n["text"]):
            path = root / n["file"].removeprefix("memory/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(n["text"], encoding="utf-8")


def _one(repo: str, months: int, token: str | None) -> dict:
    try:
        return learn(repo, months, default_rules(), token=token, qualify=True)
    except RepoError as error:
        return {"repo": repo, "error": str(error)}


def run(repos: list[str], months: int, token: str | None) -> tuple[list[dict], list[dict]]:
    # Processes, not threads: opening a vault sets an environment variable for Relate.
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(partial(_one, months=months, token=token), repos))
    return [r for r in results if "error" not in r], [r for r in results if "error" in r]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--out", default=str(ROOT / "docs" / "real-repos.json"))
    ap.add_argument("--vault", default=str(ROOT / "bench" / "real-memory"))
    args = ap.parse_args()
    repos = [r.strip() for r in REPOS.read_text().splitlines() if r.strip()]
    token = os.environ.get("GITHUB_READ_TOKEN")
    done, failed = run(repos, args.months, token)

    if not done:
        raise SystemExit("no repository could be read; the vault and report are unchanged")
    vault = Path(args.vault)
    if vault.exists() and not {p.name for p in vault.iterdir()} <= LABELS:
        raise SystemExit(f"{vault} holds files other than a memory vault; pick another --vault")
    with tempfile.TemporaryDirectory() as tmp:
        before, stale, fresh = Path(tmp, "before"), Path(tmp, "stale"), Path(tmp, "fixed")
        for r in done:
            _files(r["before"], before)
            _files(r["after"], stale)
            _files(r["fixed_after"], fresh)
        whole = review_vaults(before, stale, default_rules())
        fixed = review_vaults(before, fresh, default_rules())
        shutil.rmtree(vault, ignore_errors=True)
        shutil.copytree(fresh, vault)
    with open_vault(vault) as conn:
        superseded = rows(conn, "MATCH (:Fact)-[:SUPERSEDES]->(o:Fact) RETURN count(o) AS n")
        facts = rows(conn, "MATCH (f:Fact) RETURN count(f) AS n")

    report = {
        "months": args.months,
        "repos": len(repos),
        "read": len(done),
        "with_facts": sum(1 for r in done if r["learned"] or r["proposed"]),
        "with_changes": sum(1 for r in done if r["changed"]),
        "facts_in_memory": facts[0]["n"],
        "facts_learned_then": sum(r["learned"] for r in done),
        "facts_changed": sum(len(r["changed"]) for r in done),
        "superseded_in_vault": superseded[0]["n"],
        "blocked_stale": whole.blocked,
        "blocking_findings_stale": len(whole.blocking),
        "blocked_fixed": fixed.blocked,
        "failed": [{"repo": r["repo"], "error": r["error"]} for r in failed],
        "results": [
            {
                "repo": r["repo"],
                "old": r["old"],
                "head": r["head"],
                "learned": r["learned"],
                "proposed": r["proposed"],
                "blocked": r["blocked"],
                "fixed_blocked": r["fixed"]["blocked"],
                "changed": r["changed"],
            }
            for r in sorted(done, key=lambda r: r["repo"].lower())
        ],
    }
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=1))


if __name__ == "__main__":
    main()

"""Scripted demo agents. They read the generated sample project under demo/project and
write what they learn into the shared memory through mempr, on a branch, as a pull request.

No model is called: the agents are deterministic so that anyone can rerun the demo.

  python demo/agent.py seed                       # write the starting memory (used once)
  python demo/agent.py run --scenario contradiction --push
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from mempr import memory
from mempr.memory import fact_name

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "demo" / "project"
VAULT = ROOT / "memory"
STALE_BASE = "demo-stale-base"  # tag: an older commit of main, as a stale clone would have

SCENARIOS = {
    "clean": "learns one new, sourced fact; the check passes",
    "contradiction": "trusts an outdated design note and contradicts memory",
    "supersede": "records a database move but forgets the facts derived from the old one",
    "stale-clone": "works from an old clone; git merges cleanly but the graph loses a note",
    "unsourced": "writes a fact by hand with no source and a link to a missing note",
}


def sh(*args: str, check: bool = True) -> str:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.exit(f"$ {' '.join(args)}\n{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def learn_service(path: Path, agent: str) -> None:
    spec = yaml.safe_load(path.read_text())
    source = path.relative_to(ROOT).as_posix()
    entity = spec["name"]
    for predicate, value in spec["facts"].items():
        memory.add_fact(
            VAULT, entity=entity, predicate=predicate, value=str(value), agent=agent, source=source
        )
    values = {**spec["facts"]}
    for item in spec.get("derived", []):
        values[item["predicate"]] = item["value"]
        memory.add_fact(
            VAULT,
            entity=entity,
            predicate=item["predicate"],
            value=item["value"],
            agent=agent,
            source=source,
            derived_from=[fact_name(entity, p, str(values[p])) for p in item["from"]],
        )


def seed() -> None:
    for path in sorted(PROJECT.glob("services/*/service.yaml")):
        learn_service(path, "scout")
    memory_many = VAULT / "Predicate"
    memory_many.mkdir(exist_ok=True)
    (memory_many / "owner.md").write_text(
        "---\ntype: Predicate\nname: owner\ncardinality: many\n---\n"
    )


def scenario_clean(agent: str) -> str:
    memory.add_fact(
        VAULT,
        entity="checkout-web",
        predicate="calls",
        value="payments-api",
        agent=agent,
        source="demo/project/services/checkout-web/service.yaml",
        statement="checkout-web calls payments-api to take payments.",
    )
    return "Learned that checkout-web calls payments-api."


def scenario_contradiction(agent: str) -> str:
    memory.add_fact(
        VAULT,
        entity="payments-api",
        predicate="database",
        value="mysql-8",
        agent=agent,
        source="demo/project/docs/legacy/payments-api.md",
        statement="payments-api keeps its data in MySQL 8.",
    )
    return "Read the payments-api design note and recorded its database."


def scenario_supersede(agent: str) -> str:
    memory.add_fact(
        VAULT,
        entity="payments-api",
        predicate="database",
        value="cockroachdb-24",
        agent=agent,
        source="demo/project/docs/migration-2026.md",
        statement="payments-api moves to CockroachDB 24.",
        supersedes=[fact_name("payments-api", "database", "postgres-15")],
    )
    return "Recorded that payments-api moves to CockroachDB, replacing the Postgres fact."


def scenario_stale_clone(agent: str) -> str:
    note = VAULT / "Fact" / f"{fact_name('payments-api', 'database', 'postgres-15')}.md"
    text = note.read_text()
    note.write_text(text.replace("type: Fact\n", f"type: Fact\nverified: by-{agent}\n", 1))
    return "Re-verified the payments-api database fact and stamped it."


def scenario_unsourced(agent: str) -> str:
    name = fact_name("ledger-worker", "retries", "5")
    (VAULT / "Fact" / f"{name}.md").write_text(
        f'---\ntype: Fact\nname: {name}\npredicate: retries\nstatus: active\nvalue: "5"\n'
        f'ABOUT:\n  - "[[Entity/ledger-worker]]"\nASSERTED_BY:\n  - "[[Agent/{agent}]]"\n'
        'SOURCE:\n  - "[[Source/a-chat-i-half-remember]]"\n---\n\nledger-worker retries 5 times.\n'
    )
    return "Wrote down the ledger-worker retry count from memory."


def run(scenario: str, agent: str, push: bool, base: str) -> None:
    start = STALE_BASE if scenario == "stale-clone" else base
    branch = f"agent/{agent}-{scenario}-{int(time.time())}"
    sh("git", "checkout", "-q", "-b", branch, start)
    summary = globals()[f"scenario_{scenario.replace('-', '_')}"](agent)
    sh("git", "add", "memory")
    sh(
        "git", "-c", f"user.name={agent}-agent", "-c", f"user.email={agent}@agents.invalid",
        "commit", "-q", "-m", f"memory: {summary}",
    )  # fmt: skip
    print(f"branch {branch}: {summary}")
    if not push:
        return
    sh("git", "push", "-q", "origin", branch)
    body = (
        f"Opened by the scripted demo agent `{agent}` (scenario `{scenario}`: "
        f"{SCENARIOS[scenario]}).\n\n{summary}\n\nThe memory review comments below and sets "
        "the `memory-review` check."
    )
    created = subprocess.run(
        ["gh", "pr", "create", "--base", base.removeprefix("origin/"), "--head", branch,
         "--title", f"memory: {summary}", "--body", body],
        cwd=ROOT, capture_output=True, text=True,
    )  # fmt: skip
    if created.returncode != 0:
        repo = os.environ.get("GITHUB_REPOSITORY", "<owner>/<repo>")
        sys.exit(
            f"The branch is pushed but the pull request could not be opened:\n{created.stderr}\n"
            f"Open it by hand: https://github.com/{repo}/compare/{branch}?expand=1\n"
            "To let this workflow open pull requests, tick 'Allow GitHub Actions to create and "
            f"approve pull requests' at https://github.com/{repo}/settings/actions"
        )
    url = created.stdout.strip()
    print(url)
    if os.environ.get("GITHUB_ACTIONS"):
        # Pull requests opened with the Actions token do not trigger workflows, so start it.
        number = url.rstrip("/").rsplit("/", 1)[-1]
        sh("gh", "workflow", "run", "memory-review.yml", "--ref", branch, "-f", f"pr={number}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    r = sub.add_parser("run")
    r.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    r.add_argument("--agent", default="builder")
    r.add_argument("--base", default="origin/main")
    r.add_argument("--push", action="store_true", help="push the branch and open a pull request")
    args = parser.parse_args()
    if args.command == "seed":
        seed()
    else:
        run(args.scenario, args.agent, args.push, args.base)


if __name__ == "__main__":
    main()

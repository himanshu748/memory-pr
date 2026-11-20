"""Measure what the memory review changes.

Simulated agents work on a generated project whose true facts change over time. Each
agent writes what it learned on a branch; some of them make the mistakes real agents
make (trust an outdated document, replace a fact without re-checking what was derived
from it, work from a stale clone, write a fact with no source). The same pull requests
are replayed twice:

  without the check: every branch git can merge is merged
  with the check:    a branch is merged only when `mempr check` passes

Then the memory on main is compared with the ground truth. Everything is seeded and
generated; no model is called.

  python bench/run.py --seed 7 --rounds 12 --agents 6 --out docs/benchmark.json
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import tempfile
import time
from pathlib import Path

from mempr import memory
from mempr.check import review
from mempr.memory import fact_name
from mempr.rules import default_rules
from mempr.vault import lint, open_vault, rows

SERVICES = [f"svc-{i:02d}" for i in range(12)]
PREDICATES = {
    "database": ["postgres-15", "mysql-8", "cockroachdb-24", "sqlite-3"],
    "runtime": ["python-3.12", "node-22", "go-1.23", "java-21"],
    "queue": ["kafka", "sqs", "rabbitmq", "nats"],
    "region": ["eu-west-1", "us-east-1", "ap-south-1", "eu-central-1"],
}
DERIVED = {"database": ["backup-job", "migration-tool"], "queue": ["dead-letter-policy"]}
MISTAKES = ["outdated-doc", "supersede-only", "stale-clone", "unsourced"]


class Sim:
    def __init__(self, path: Path, rules, with_check: bool):
        self.path, self.vault, self.rules, self.with_check = (
            path,
            path / "memory",
            rules,
            with_check,
        )
        self.merged = self.blocked = self.git_conflicts = self.good_blocked = self.fixed = 0
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "bench@example.invalid")
        self.git("config", "user.name", "bench")
        (path / ".gitignore").write_text(".relate/\n.cypherlite.*\nindex.md\nlog.md\n")

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True, check=check
        )

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def add(self, entity, predicate, value, agent, **kw) -> str:
        return memory.add_fact(
            self.vault, entity=entity, predicate=predicate, value=value, agent=agent,
            source=f"services/{entity}/service.yaml", **kw,
        )  # fmt: skip

    def submit(self, branch: str, good: bool) -> None:
        """Merge the branch into main the way the arm allows."""
        if self.with_check:
            result = review(self.path, "main", branch, "memory", self.rules)
            stale = [f for f in result.blocking if f.rule.id == "stale-derivation"]
            if stale and len(stale) == result.problem_count:
                # The agent follows the review: it retracts the facts that rested on the
                # replaced one and pushes again.
                self.git("checkout", "-q", branch)
                for name in sorted({f.row["fact"] for f in stale}):
                    memory.retract(self.vault, name)
                self.commit("retract facts derived from the replaced fact")
                self.fixed += 1
                result = review(self.path, "main", branch, "memory", self.rules)
            if result.blocked:
                self.blocked += 1
                self.good_blocked += good
                return
        self.git("checkout", "-q", "main")
        merge = self.git("merge", "-q", "--no-ff", "-m", f"merge {branch}", branch, check=False)
        if merge.returncode != 0:
            self.git("merge", "--abort", check=False)
            self.git_conflicts += 1
            return
        self.merged += 1


def derived_value(predicate: str, value: str) -> str:
    return f"{predicate}-for-{value}"


def play(sim: Sim, seed: int, rounds: int, agents: int, mistake_rate: float) -> dict[str, dict]:
    rng = random.Random(seed)
    truth = {s: {p: rng.choice(v) for p, v in PREDICATES.items()} for s in SERVICES}
    known: set[tuple[str, str]] = set()
    for service in SERVICES[:4]:
        for predicate in ("database", "queue"):
            sim.add(service, predicate, truth[service][predicate], "scout")
            for d in DERIVED[predicate]:
                sim.add(
                    service, d, derived_value(d, truth[service][predicate]), "scout",
                    derived_from=[fact_name(service, predicate, truth[service][predicate])],
                )  # fmt: skip
            known.add((service, predicate))
    sim.commit("seed")
    previous_main = sim.git("rev-parse", "HEAD").stdout.strip()

    for rnd in range(rounds):
        round_start = sim.git("rev-parse", "main").stdout.strip()
        branches = []
        for a in range(agents):
            agent = f"agent-{a}"
            branch = f"agent/{rnd}-{a}"
            mistake = rng.choice(MISTAKES) if rng.random() < mistake_rate else None
            start = previous_main if mistake == "stale-clone" else round_start
            sim.git("checkout", "-q", "-B", branch, start)
            service, predicate = rng.choice(sorted(known))
            current = truth[service][predicate]
            wrong = rng.choice([v for v in PREDICATES[predicate] if v != current])
            if mistake == "outdated-doc":
                sim.add(service, predicate, wrong, agent)
            elif mistake == "supersede-only" and predicate in DERIVED:
                truth[service][predicate] = wrong  # the system really changed
                sim.add(
                    service, predicate, wrong, agent,
                    supersedes=[fact_name(service, predicate, current)],
                )  # fmt: skip
            elif mistake == "stale-clone":
                note = sim.vault / "Fact" / f"{fact_name(service, predicate, current)}.md"
                if note.exists():
                    text = note.read_text()
                    line = "type: Fact\n" if a % 2 else f"value: {current}\n"
                    stamp = f"checked: by-{agent}\n"
                    new = text.replace(line, line + stamp, 1)
                    note.write_text(new)
            elif mistake == "unsourced":
                name = fact_name(service, "port", str(8000 + a))
                (sim.vault / "Fact" / f"{name}.md").write_text(
                    f"---\ntype: Fact\nname: {name}\npredicate: port\nstatus: active\n"
                    f'value: "{8000 + a}"\nABOUT:\n  - "[[Entity/{service}]]"\n---\n'
                )
            else:
                new_service = rng.choice(SERVICES)
                new_predicate = rng.choice(sorted(PREDICATES))
                if (new_service, new_predicate) not in known:
                    sim.add(new_service, new_predicate, truth[new_service][new_predicate], agent)
                    known.add((new_service, new_predicate))
            sim.commit(f"{agent} round {rnd}: {mistake or 'correct'}")
            branches.append((branch, mistake is None))
        previous_main = round_start
        for branch, good in branches:
            sim.submit(branch, good)
    sim.git("checkout", "-q", "main")
    return truth


def measure(sim: Sim, truth: dict[str, dict]) -> dict:
    issues, _ = lint(sim.vault)
    with open_vault(sim.vault) as conn:
        active = rows(
            conn,
            "MATCH (f:Fact)-[:ABOUT]->(e:Entity) WHERE f.status = 'active' AND NOT EXISTS { "
            "MATCH (n:Fact)-[:SUPERSEDES]->(f) WHERE n.status = 'active' } "
            "RETURN e.name AS entity, f.predicate AS predicate, f.value AS value",
        )
        found = {rule.id: len(rows(conn, rule.cypher)) for rule in sim.rules}
    wrong = sum(
        1
        for f in active
        if f["predicate"] in PREDICATES and truth[f["entity"]][f["predicate"]] != f["value"]
    )
    answers: dict[tuple[str, str], set[str]] = {}
    for f in active:
        answers.setdefault((f["entity"], f["predicate"]), set()).add(f["value"])
    asked = [(s, p) for s in SERVICES for p in PREDICATES]
    correct = sum(1 for s, p in asked if answers.get((s, p)) == {truth[s][p]})
    misled = sum(1 for s, p in asked if answers.get((s, p), set()) - {truth[s][p]})
    return {
        "pull_requests_merged": sim.merged,
        "pull_requests_blocked": sim.blocked,
        "pull_requests_fixed_after_review": sim.fixed,
        "correct_pull_requests_blocked": sim.good_blocked,
        "git_conflicts": sim.git_conflicts,
        "wrong_active_facts": wrong,
        "contradictions": found["contradiction"],
        "facts_resting_on_replaced_facts": found["stale-derivation"],
        "notes_emptied_by_a_clean_merge": sum(i.kind == "duplicate_key" for i in issues),
        "unsourced_facts": found["unsourced-fact"],
        "questions_answered_correctly": correct,
        "questions_answered_wrongly_or_ambiguously": misled,
        "questions_asked": len(asked),
    }


def run(seed: int, rounds: int, agents: int, mistake_rate: float) -> dict:
    rules = default_rules()
    result = {}
    for arm, with_check in (("without_check", False), ("with_check", True)):
        with tempfile.TemporaryDirectory(prefix="mempr-bench-") as tmp:
            sim = Sim(Path(tmp), rules, with_check)
            truth = play(sim, seed, rounds, agents, mistake_rate)
            result[arm] = measure(sim, truth)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--seeds", type=int, default=1, help="number of seeds, starting at --seed")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--agents", type=int, default=6)
    parser.add_argument("--mistake-rate", type=float, default=0.3)
    parser.add_argument("--out", help="write the results as JSON")
    args = parser.parse_args()
    started = time.time()
    runs = {
        s: run(s, args.rounds, args.agents, args.mistake_rate)
        for s in range(args.seed, args.seed + args.seeds)
    }
    total = {
        arm: {k: sum(r[arm][k] for r in runs.values()) for k in next(iter(runs.values()))[arm]}
        for arm in ("without_check", "with_check")
    }
    report = {
        "config": {**vars(args), "pull_requests": args.rounds * args.agents * args.seeds},
        "total": total,
        "per_seed": runs,
        "seconds": round(time.time() - started, 1),
    }
    report["config"].pop("out")
    print(f"{'metric':<46}{'without check':>14}{'with check':>12}")
    for key in total["with_check"]:
        print(f"{key:<46}{total['without_check'][key]:>14}{total['with_check'][key]:>12}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

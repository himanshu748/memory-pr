# Benchmarks

[Back to README](../README.md)

Two measurements back the project claims. The 50-repository scan reads real public
files on GitHub. The 300-pull-request replay is **simulated**: scripted agents on a
generated project — it measures the check, not real models. For real model-driven
runs see [`docs/EVIDENCE.md`](EVIDENCE.md).

## Measured on 50 real repositories

An agent's memory of 50 popular public GitHub repositories (Go, Rust, Python, Node and
Docker projects, from `tokio` and `fastapi` to `gitea` and `opentelemetry-demo`), learned
from their real files a year ago and compared with what the same files say today:

| | |
| --- | --- |
| Repositories read | 50 |
| Facts learned a year ago | 328 |
| Of those, wrong today | 154 (47%), in 36 repositories |
| Wrong facts the check blocked | 154 |
| Blocked after each new fact `SUPERSEDES` the old one | 0 |

Typical moves: `fastapi` now requires Python 3.11 instead of 3.8, `n8n` Node 24 instead of
22.16, `traefik` builds on Alpine 3.24 instead of 3.22. Each fact links to the file at the
commit it was read from.

Reproduce: `GITHUB_READ_TOKEN=$(gh auth token) python bench/real.py` (a few minutes; the
repositories are listed in [`bench/repos.txt`](../bench/repos.txt), and the numbers change as
the repositories do). Per-repository results, with every changed value and both source
links, are in [`docs/real-repos.json`](real-repos.json); the memory itself, with each
new fact superseding the stale one, is the Relate vault [`bench/real-memory/`](../bench/real-memory/):

```bash
mempr --vault bench/real-memory query "MATCH (new:Fact)-[:SUPERSEDES]->(old:Fact) RETURN old.value, new.value LIMIT 5"
```

## Simulated benchmark: 300 pull requests

300 pull requests from **simulated** agents (5 seeds, 10 rounds, 6 agents per round,
30% of pull requests carry one of four mistakes), replayed with and without the check.
At the end a new agent asks the memory about every service.

| | Without the check | With the check |
| --- | --- | --- |
| Pull requests merged | 299 | 254 |
| Pull requests blocked | 0 | 46 |
| Pull requests fixed after the review, then merged | 0 | 8 |
| Correct pull requests blocked by mistake | 0 | 0 |
| Wrong facts in memory at the end | 21 | 1 |
| Contradictions in memory | 25 | 0 |
| Facts resting on a replaced fact | 8 | 0 |
| Notes emptied by a merge git called clean | 3 | 0 |
| Facts with no source | 20 | 0 |
| Questions answered correctly (of 240) | 139 | 160 |
| Questions answered wrongly or with two values | 19 | 1 |

Reproduce: `python bench/run.py --seed 1 --seeds 5 --rounds 10 --agents 6 --out docs/benchmark.json`
(about 2 minutes). The agents, the project and its ground truth are generated in
[`bench/run.py`](../bench/run.py); raw numbers are in [`docs/benchmark.json`](benchmark.json).
The questions left unanswered in both columns are facts no agent was asked to learn.
The one wrong fact that remains with the check is a true change whose pull request
stayed blocked, so the memory still holds the older value.

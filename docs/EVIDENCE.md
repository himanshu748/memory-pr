# Evidence log

[Back to README](../README.md)

Every numeric claim in the README and this file was produced by a command listed
here, run 24-25 Nov 2026. Raw transcripts are reproduced verbatim where short.

## One-command quickstart (deliverable: fresh clone)

Command (the only prerequisite is Python 3.10+, for the bundled static server):

```bash
./quickstart.sh
```

Verified from a scratch clone with no prior state:

```bash
$ git clone https://github.com/himanshu748/memory-pr /tmp/mp-fresh && cd /tmp/mp-fresh
$ ./quickstart.sh
serving http://localhost:8899 (Ctrl-C to stop)
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:8899/
200
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:8899/data/demo.json
200
```

The page opened in the browser rendered the playground: "Relate is running in this
page", the five-agent pull-request explorer, and the Cypher console — all driven by
`site/data/demo.json`, no credentials or environment variables.

Occupied-port behavior (regression check, 25 Nov):

```bash
$ python3 -m http.server 8619 -d /tmp &   # occupy the port first
$ ./quickstart.sh 8619
error: port 8619 is already serving something else.
retry with a free port, e.g.: ./quickstart.sh 8620
(exit status 1 — the browser is never opened at the wrong service)
```

## Rendered README check

The branch README rendered on github.com at a 1024×768 scaled desktop viewport
(2560×1920 native): the whole file is 90 lines / 4.92 KB; hero + GIF + quickstart
visible in the first screen, everything else in the second. Screenshot captured
25 Nov 2026.

## Test count

Main at [`fa507d7`](https://github.com/himanshu748/memory-pr/commit/fa507d741624016f800db53e1d8a6a10fdcb96ad), checked on 25 Nov 2026:

```text
$ python -m pytest -q -p no:cacheprovider --basetemp <scratch>/boost-memory-pytest
277 passed in 78.94s (0:01:18)
$ node --test tests/js/*.test.mjs
ℹ tests 13
ℹ pass 13
ℹ fail 0
```

Earlier run from the README rework:

```bash
$ pytest -q
........................................................................ [ 85%]
.....................................                                    [100%]
253 passed in 44.73s
```

Run 25 Nov 2026 on branch `devin/1795550867-judge-polish` (the commit that adds
this file), Python 3.12 venv at `.venv/`.

```bash
$ node --test tests/js/*.test.mjs
# pass 13
# fail 0
```

## Real agent run (A/B)

Two sequential agent runs on the public fixture
[`himanshu748/memory-pr-ab-fixture`](https://github.com/himanshu748/memory-pr-ab-fixture),
same clone of the fixture, same `mempr` install, same prompt (verbatim in
[`demo/ab/RUNBOOK.md`](../demo/ab/RUNBOOK.md) §3). The only variable: run A's checkout
carried **stale** shared memory (`demo/run-a` branch, `payments-api database =
postgres-15` active), run B's carried **reviewed** memory (`demo/run-b`,
`postgres-15` retracted, `mongodb-8` active and sourced).

Scope, stated plainly: this A/B evidence shows **reviewed memory changing what an
agent decides** — nothing more. Both runs were executed by one model (Devin)
back-to-back in isolated checkouts — spawning two independent Devin sessions was
impossible because the organization was out of quota (HTTP 403 `out_of_quota` on
`POST /v3/organizations/org_26wI5SzJHOJ806YQ/sessions`, 24-25 Nov). The fixture
repository has no `memory-review` workflow, so the checks below were run locally
with `mempr check` on the exact pushed commits and posted as PR comments; the
shared-memory workflow on GitHub itself is demonstrated by the independent PRs in
the next section. Re-running the A/B pair with two separate sessions needs only the
commands in the runbook.

## The shared-memory workflow on GitHub (independent PRs)

Real pull requests in this repository, each opened independently and each carrying
the real `memory-review` status check:

- [PR #60](https://github.com/himanshu748/memory-pr/pull/60) — **blocked**:
  git merges the branch cleanly, and the review shows the merge silently dropping
  3 links, 4 properties plus the duplicate-key note Relate reads as empty. This is
  the failure the README GIF opens with.
- [PR #15](https://github.com/himanshu748/memory-pr/pull/15) — **blocked**:
  a database contradiction, with the `SUPERSEDES` fix spelled out in the comment.
- [PR #6](https://github.com/himanshu748/memory-pr/pull/6) — **passes** against
  current main: the same check green on a correct proposal.

### What each agent read

```bash
$ mempr --repo <run-a> recall payments-api
payments-api database = postgres-15  [payments-api-demo-service-yaml; by scout]

$ mempr --repo <run-b> recall payments-api
payments-api database = mongodb-8  [docker-compose-yml; by reviewer]
```

### What each agent proposed

- **Run A** — [PR #1](https://github.com/himanshu748/memory-pr-ab-fixture/pull/1),
  base `demo/run-a`: added `postgres:15` + `DATABASE_URL=postgresql://…`, and
  hand-wrote `Fact/payments-api--dev-database--postgres-15` with no `SOURCE` and an
  `ASSERTED_BY` link to a non-existent `Agent/builder` note.
- **Run B** — [PR #2](https://github.com/himanshu748/memory-pr-ab-fixture/pull/2),
  base `demo/run-b`: added `mongo:8` + `DATABASE_URL=mongodb://…`, and recorded
  `dev-database = mongodb-8` with a `SOURCE` link and an `Agent/builder` note.

### What the check did

Run A — `mempr check --base demo/run-a --head agent/run-a` (full output posted as a
comment on the PR):

```
## Memory review: blocked (2 problems)
This pull request adds 1 note, 2 links, 4 properties and removes nothing.
### 1. Links to a note that does not exist
Fact/payments-api--dev-database--postgres-15 -[:ASSERTED_BY]-> Agent/builder ...
### 2. Fact has no source
Fact/payments-api--dev-database--postgres-15 is active but has no `SOURCE` link ...
```

Run B — `mempr check --base demo/run-b --head agent/run-b`:

```
## Memory review: passed
This pull request adds 3 notes, 3 links, 5 properties and removes nothing.
```

### The correction the reviewed memory came from

The `demo/run-b` vault was produced with real `mempr` commands and the check passed
the transition (verified in a scratch clone, 24 Nov):

```bash
$ mempr --repo <review> retract payments-api--database--postgres-15
$ mempr --repo <review> add-fact --entity payments-api --predicate database \
    --value mongodb-8 --agent reviewer --source <db.py URL> \
    --supersedes payments-api--database--postgres-15
$ mempr --repo <review> check --base <stale-sha> --head <fix-sha>
## Memory review: passed
```

### Reproduce

[`demo/ab/RUNBOOK.md`](../demo/ab/RUNBOOK.md) §§1-4 are the exact commands; the
published fixture replaces the `git archive` step — clone
`himanshu748/memory-pr-ab-fixture` at `demo/run-a` / `demo/run-b` and hand the agent
the §3 prompt. Raw diffs: `git show` on each PR's head commit.

## Other real evidence already on GitHub

- [PR #15](https://github.com/himanshu748/memory-pr/pull/15) — blocked by the
  `memory-review` check (contradiction), comment by `github-actions[bot]`.
- [PR #6](https://github.com/himanshu748/memory-pr/pull/6) — the same check passing.
- [`docs/real-repos.json`](real-repos.json) — the 50-repository scan, per repository,
  each changed value with both source links. Reproduce:
  `GITHUB_READ_TOKEN=$(gh auth token) python bench/real.py`.
- [`docs/benchmark.json`](benchmark.json) — the **simulated** 300-PR replay.
  Reproduce: `python bench/run.py --seed 1 --seeds 5 --rounds 10 --agents 6`.

# A/B demo: does reviewed memory change what the agent does?

One task, two fresh agent sessions, identical repo, commit, model and prompt.
The only variable is the memory the agent reads — and each session gets it the
same way, through `mempr recall`.

- **Run A — stale memory.** `memory-stale/` says `payments-api database = postgres-15`,
  status `active`, sourced to an old `service.yaml` deleted from the repo since.
- **Run B — reviewed memory.** The same fact marked `retracted`, superseded by
  `database = mongodb-8` whose SOURCE points at the current `docker-compose.yml`.
  The runbook derives this state with `mempr` commands, and `mempr check` passes
  it — the reviewed memory is what a real accepted review produces, not a
  hand-written fixture.

## 1. Publish the fixture repo once

`fixture/` is a runnable service whose current code already uses MongoDB
(`src/db.py` imports `pymongo`; `docker compose up --build` works as shipped —
`/db` answers 503 until a database service and `DATABASE_URL` exist, which is
exactly what the agent is asked to add).

Build the repo with two commits so the evidence links can be pinned:

```bash
cd demo/ab
mkdir -p fixture/services/payments-api
cp old-evidence/services/payments-api/service.yaml fixture/services/payments-api/
cd fixture
git init -b main && git add -A && git commit -m "initial deploy (postgres)"
git rm -r services && git commit -m "migrate payments-api to mongodb"
OLD_SHA=$(git rev-parse HEAD~1); HEAD_SHA=$(git rev-parse HEAD)
# create github.com/himanshu748/memory-pr-ab-fixture (public), push, then:
cd ..
sed -i "s/OLD_SHA/$OLD_SHA/; s/HEAD_SHA/$HEAD_SHA/" \
  memory-stale/Source/*.md memory-reviewed/Source/*.md
git -C fixture status   # tree is clean — the sed touched the vaults, not the repo
```

`OLD_SHA`'s `service.yaml` resolves only at the pinned commit (it was deleted
after) — the stale link is a real 404 on `main`, a real page at the pin.
`HEAD_SHA`'s `docker-compose.yml` resolves at both. A public repo is required
so judges can replay it.

## 2. Produce the reviewed memory on camera (60 seconds)

In a scratch clone of the fixture repo, copy `memory-stale/` in as `memory/`,
commit it, pin that commit, then run the two commands a conscientious agent
would after noticing the source file is gone:

```bash
git init -b main && cp -r ../memory-stale memory && git add -A \
  && git commit -m "seed shared memory"
STALE_SHA=$(git rev-parse HEAD)     # pin BEFORE the fix — main moves
mempr retract payments-api--database--postgres-15
mempr add-fact --entity payments-api --predicate database --value mongodb-8 \
  --agent reviewer --source docker-compose.yml \
  --supersedes payments-api--database--postgres-15
git add memory && git commit -m "retract stale database fact, record mongodb-8"
FIX_SHA=$(git rev-parse HEAD)
mempr check --base $STALE_SHA --head $FIX_SHA   # passes: correction accepted
```

Do not use `main` as the base — after the fix commit, `main` and `HEAD` point
at the same revision and the check reports no changes. The pinned stale SHA
is the only base that shows the retraction and the superseding fact.

Capture the `mempr check` output passing — that is the proof the reviewed
memory is a legitimate review result. `memory-reviewed/` in this directory is
a reference copy of the expected end state; diff it against what the commands
produced.

## 3. The two runs

Fresh clone at `HEAD_SHA`, fresh agent session (same harness, same model —
e.g. Claude Code, Codex or Devin), `memory/` seeded per run and committed on a
working branch so the agent trusts it:

- Run A: `memory-stale/` → `memory/`
- Run B: the corrected `memory/` the scratch clone holds at `$FIX_SHA` — the
  exact vault `mempr check` accepted, not the prewritten `memory-reviewed/`

Both sessions install `mempr` (pip or the composite Action's tooling) and run
the identical prompt:

```
The payments-api service in this repo needs a database for local development.
Add the database service to docker-compose.yml and a matching DATABASE_URL
line in .env. The notes under memory/ are the team's shared agent memory —
earlier agents wrote them; run `mempr recall payments-api` to see what it
knows. In the commit message, cite where the database choice came from.
```

Do not hint at MongoDB or Postgres. If the agent asks to clarify, answer
"check the shared memory" or let it decide.

## 4. What to capture

| Shot | From |
| --- | --- |
| `mempr check --base --head` passing the correction | step 2 |
| `mempr recall payments-api` output | both sessions (A shows postgres-15; B shows mongodb-8 — recall prints only current facts; the retraction shows in the graph) |
| `git show` of the agent's commit — compose + .env diff and the citation line | both sessions (the agent commits, so `git diff` is empty) |
| `curl localhost:8080/db` after `docker compose up --build` | both sessions — whether the chosen database actually answers |
| The `SUPERSEDES`/`SOURCE` path in the graph | run B memory in the playground or `mempr export` |

## 5. Expected outcome

- **A** adds `postgres:15` + `DATABASE_URL=postgresql://…`, citing
  `memory/Fact/payments-api--database--postgres-15.md`. The edit is wrong — the
  code uses MongoDB — and `/db` cannot answer (no postgres driver, no server).
- **B** adds `mongo:8` + `DATABASE_URL=mongodb://…`, citing `src/db.py` (the
  `pymongo` import — the starting compose file leaves the database a TODO) or
  the reviewed fact's SOURCE link, because `mempr recall` returns `mongodb-8`
  as the current value. `/db` pings MongoDB.

If A lands on MongoDB anyway, publish that — "the model recovered without
reviewed memory in this run" is honest and still shows B's provenance.

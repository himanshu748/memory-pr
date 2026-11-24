# A/B demo: does reviewed memory change what the agent does?

One task, two fresh agent sessions, identical repo, commit, model and prompt.
The only variable is the memory the agent reads.

- **Run A — stale memory.** `memory-stale/` says `payments-api database = postgres-15`,
  status `active`, sourced to an old `service.yaml` that no longer reflects the repo.
- **Run B — reviewed memory.** `memory-reviewed/` carries the same fact marked
  `retracted`, superseded by `database = mongodb-8` whose SOURCE points at the
  current `docker-compose.yml`. This is the state `mempr check` leaves a memory in.

## 1. Publish the fixture repo once

`fixture/` is a three-file service whose current code already uses MongoDB
(`src/db.py` imports `pymongo`; the README and compose template say so).
Push it as a **public** repo so judges can replay it, then pin the commit:

```bash
cd demo/ab/fixture
git init -b main && git add -A && git commit -m "payments-api demo service"
# create himanshu748/payments-api-demo (public), push, then:
git rev-parse HEAD   # this is the commit both runs check out
```

A private repo works for the recording but a public one survives judging.

## 2. Run A — the stale session

Fresh clone, fresh agent session (same harness, same model — e.g. Claude Code,
Codex or Devin). Copy `memory-stale/` into the clone as `memory/` and commit it
on a working branch so the agent trusts it as shared memory.

## 3. Run B — the reviewed session

Identical, but copy `memory-reviewed/` into `memory/` instead.

## 4. The prompt — verbatim, identical in both runs

```
The payments-api service in this repo needs a database for local development.
Add the database service to docker-compose.yml and a matching DATABASE_URL
line in .env.example. The notes under memory/ are the team's shared agent
memory — earlier agents wrote them, use them. In the commit message, cite
where the database choice came from.
```

Do not hint at MongoDB or Postgres in the prompt. Do not intervene if the agent
asks to clarify — answer "check the shared memory" or let it decide.

## 5. What to capture

| Shot | From |
| --- | --- |
| `git diff` of docker-compose.yml + .env.example | both sessions, end |
| The commit message's citation line | both sessions |
| The source each agent quotes mid-run | session transcript |
| `memory/Fact/payments-api--database--postgres-15.md` open next to the run A diff | run A |
| The `SUPERSEDES` path in the graph (`Fact → Fact`, `Fact → Source`) | run B memory in the playground or `mempr query` |

## 6. Expected outcome

- **A** adds a `postgres:15` service and `DATABASE_URL=postgresql://…`, citing
  `memory/Fact/payments-api--database--postgres-15.md`. The edit is wrong: the
  repo's code uses MongoDB.
- **B** adds a `mongo:8` service and `DATABASE_URL=mongodb://…`, citing
  `src/db.py` or the reviewed fact's SOURCE link — because the stale fact is
  marked `retracted` and superseded.

If A also lands on MongoDB anyway, that is still publishable — report it
("the model recovered without reviewed memory in this run") rather than
rerunning until it fails.

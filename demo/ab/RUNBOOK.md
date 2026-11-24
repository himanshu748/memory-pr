# A/B demo: does reviewed memory change what the agent does?

One task, two fresh agent sessions, identical repo, commit, model and prompt.
The only variable is the memory the agent reads — and each session gets it the
same way, through `mempr recall`.

- **Run A — stale memory.** `memory-stale/` says `payments-api database = postgres-15`,
  status `active`, sourced to a historical sample manifest outside the current fixture.
- **Run B — reviewed memory.** The same fact marked `retracted`, superseded by
  `database = mongodb-8` whose SOURCE points at the pinned `src/db.py`.
  The runbook builds this vault with `mempr` commands and checks the correction
  before the agent reads it.

## 1. Set up the published fixture

The fixture is published under `demo/ab/fixture/` in the public memory-pr
repository. Both runs copy the same pinned snapshot into an isolated Git
repository. `src/db.py` uses PyMongo, and the fixture README records MongoDB 8
as the target. `/db` answers 503 until the agent supplies a database and
`DATABASE_URL`.

From the root of a memory-pr clone, run these commands in the same shell.
Install the CLI with `python -m pip install -e .` first.

```bash
DEMO_DIR="$(pwd)/demo/ab"
FIXTURE_REV=0a0bc4f875446efa487193b84a905d8c6687fc7f
WORK_DIR=$(mktemp -d)
FIXTURE_DIR="$WORK_DIR/demo/ab/fixture"
MONGO_SOURCE="https://github.com/himanshu748/memory-pr/blob/$FIXTURE_REV/demo/ab/fixture/src/db.py"

git cat-file -e "$FIXTURE_REV^{commit}" 2>/dev/null || git fetch origin "$FIXTURE_REV"
git archive "$FIXTURE_REV" demo/ab/fixture | tar -x -C "$WORK_DIR"
git -C "$FIXTURE_DIR" init -b main
git -C "$FIXTURE_DIR" add -A
git -C "$FIXTURE_DIR" commit -m "Seed payments-api demo fixture"
```

The two source links point to the [historical sample manifest](https://github.com/himanshu748/memory-pr/blob/0a0bc4f875446efa487193b84a905d8c6687fc7f/demo/ab/old-evidence/services/payments-api/service.yaml)
and the [current fixture's database code](https://github.com/himanshu748/memory-pr/blob/0a0bc4f875446efa487193b84a905d8c6687fc7f/demo/ab/fixture/src/db.py).
The agent checkouts contain the current fixture only.

## 2. Produce the reviewed memory on camera (60 seconds)

In a scratch clone, commit the stale memory, pin that commit, and review the
correction against it.

```bash
REVIEW_DIR="$WORK_DIR/review"
git clone "$FIXTURE_DIR" "$REVIEW_DIR"
printf '/memory/.relate/\n/memory/.cypherlite.*\n' >> "$REVIEW_DIR/.git/info/exclude"
cp -R "$DEMO_DIR/memory-stale" "$REVIEW_DIR/memory"
git -C "$REVIEW_DIR" add memory
git -C "$REVIEW_DIR" commit -m "Seed shared memory"
STALE_SHA=$(git -C "$REVIEW_DIR" rev-parse HEAD)
mempr --repo "$REVIEW_DIR" retract payments-api--database--postgres-15
mempr --repo "$REVIEW_DIR" add-fact --entity payments-api \
  --predicate database --value mongodb-8 --agent reviewer --source "$MONGO_SOURCE" \
  --supersedes payments-api--database--postgres-15
git -C "$REVIEW_DIR" add memory
git -C "$REVIEW_DIR" commit -m "Retract stale fact and record MongoDB"
FIX_SHA=$(git -C "$REVIEW_DIR" rev-parse HEAD)
mempr --repo "$REVIEW_DIR" check --base "$STALE_SHA" --head "$FIX_SHA"
```

Capture the passing `mempr check` output with its nonzero graph changes.
`memory-reviewed/` is a reference for the expected facts. The next step uses
the exact vault at `FIX_SHA`.

## 3. The two runs

Create both checkouts from the same fixture commit. Export the memory from the
two commits the check compared.

```bash
git clone "$FIXTURE_DIR" "$WORK_DIR/run-a"
git clone "$FIXTURE_DIR" "$WORK_DIR/run-b"
git -C "$REVIEW_DIR" archive "$STALE_SHA" memory | tar -x -C "$WORK_DIR/run-a"
git -C "$REVIEW_DIR" archive "$FIX_SHA" memory | tar -x -C "$WORK_DIR/run-b"
git -C "$WORK_DIR/run-a" add memory
git -C "$WORK_DIR/run-a" commit -m "Seed shared memory"
git -C "$WORK_DIR/run-b" add memory
git -C "$WORK_DIR/run-b" commit -m "Seed shared memory"
```

Open a fresh agent session in each checkout with the same harness, model, and
`mempr` installation on `PATH`. Give both sessions the same prompt.

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
| `mempr check` passing the correction | step 2 |
| `mempr recall payments-api` output | both sessions (A shows postgres-15; B shows mongodb-8 — recall prints only current facts; the retraction shows in the graph) |
| `git show` of the agent's commit — compose + .env diff and the citation line | both sessions (the agent commits, so `git diff` is empty) |
| `curl localhost:8080/db` after `docker compose up --build -d` | both sessions, once the service starts. Run `docker compose down` before switching checkouts. |
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

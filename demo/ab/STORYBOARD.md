# First 20 seconds — video storyboard

The order: the outcome first, the mechanism second. Nothing in the captions
claims memory-pr made an edit — the agents do the work; memory-pr decides what
the memory they read contains.

## 0:00–0:04 — Cold open: two `git show` outputs, side by side

Split screen, no narration needed yet. Both agents committed; `git diff` would
be empty, so show `git show` of each commit.

- Left: run A's diff — `+ postgres:15` service, `+ DATABASE_URL=postgresql://…`
- Right: run B's diff — `+ mongo:8` service, `+ DATABASE_URL=mongodb://…`

Caption: "Same repo. Same prompt. Same model. Same `mempr recall`. Different memory."

## 0:04–0:10 — What each agent's recall returned

Crop the two `mempr recall payments-api` outputs — recall prints only the
current facts, so the two lines are the whole contrast:

- A: `payments-api database = postgres-15`
- B: `payments-api database = mongodb-8`

Then one beat on run B's graph (`mempr export` or the playground): the
`postgres-15` node marked `retracted`, a `SUPERSEDES` edge from `mongodb-8`
to `postgres-15`, and a `SOURCE` edge from `mongodb-8` to the pinned
`src/db.py`. The graph shows the retraction.

Caption: "One agent read a fact the review never corrected. One read the correction."

## 0:10–0:15 — The correction was accepted

The `mempr check --base "$STALE_SHA" --head "$FIX_SHA"` output from step 2 of the runbook: the
retraction + superseding fact passes review.

Caption: "memory-pr didn't write anything. It reviewed the correction — and passed it."

## 0:15–0:20 — The mechanism

Playground graph, explorer open on a pull request that drops a source link:
`Source → Fact → Entity` path highlighted, review verdict "Blocked. This merge
would silently drop 3 links."

Caption: "Every memory change is a pull request. The graph decides, not the diff."

## Optional tag (0:20+)

`curl localhost:8080/db` in run B answering `{"ok": true}` from MongoDB, then
the repo URL + "MIT, `uses: himanshu748/memory-pr@v1`".

## Recording notes

- Film run A first, keep it under 60s total; trim agent thinking, keep the
  `mempr recall` output and the commit.
- If run A recovers and picks MongoDB anyway, keep it — "the model recovered
  without reviewed memory" is honest and still shows B's provenance.
- Capture `git show`, not `git diff` — the agent commits its work.
- Show real terminal output, not a retyped slide.

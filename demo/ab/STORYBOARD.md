# First 20 seconds — video storyboard

The order: the outcome first, the mechanism second.

## 0:00–0:04 — Cold open: two diffs, side by side

Split screen, no narration needed yet.

- Left: run A's `git diff` — `+ postgres:15` service, `+ DATABASE_URL=postgresql://…`
- Right: run B's `git diff` — `+ mongo:8` service, `+ DATABASE_URL=mongodb://…`

Caption: "Same repo. Same prompt. Same model. Different memory."

## 0:04–0:10 — The citation each agent gave

Show the two commit messages, cropped to the citation line.

- A: "database choice from memory/Fact/payments-api--database--postgres-15.md"
- B: "from docker-compose.yml / src/db.py"

Caption: "One trusted stale memory. One traced the fact to its source."

## 0:10–0:15 — Why: the reviewed fact

Open `memory-reviewed/Fact/payments-api--database--mongodb-8.md`; zoom on
`status: active`, `SUPERSEDES: postgres-15`, `SOURCE: docker-compose.yml`.
Next to it, the same fact in `memory-stale/` with `status: active`.

Caption: "memory-pr marked the old fact retracted. Git called the merge clean."

## 0:15–0:20 — The mechanism

Playground graph, explorer open on a pulled request that drops a source link:
`Source → Fact → Entity` path highlighted, review verdict "Blocked. This merge
would silently drop 3 links."

Caption: "Every memory change is a pull request. The graph decides, not the diff."

## Optional tag (0:20+)

Playground real-repo tab or `mempr check` blocking a PR on GitHub, then the
repo URL + "MIT, `uses: himanshu748/memory-pr@v1`".

## Recording notes

- Film run A first, keep it under 60s total; trim agent thinking, keep the
  decision + citation.
- If run A recovers and picks MongoDB anyway, keep it — "the model recovered
  without reviewed memory" is honest and still shows B's provenance.
- Show real `git diff` output, not a retyped slide.

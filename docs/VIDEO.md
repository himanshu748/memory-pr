# 90-second video script

[Back to README](../README.md)

Shot list keyed to captured artifacts. Narration in quotes; actions in plain text.
Total 90 s. Every visual named below exists and is linked.

| Time | Shot | Narration |
| --- | --- | --- |
| 0:00-0:04 | Full-screen text: "A text diff can't show what a memory change breaks." | — |
| 0:04-0:12 | Split screen. Left: `git merge` output "Merge made by the 'ort' strategy" / "clean". Right: the hero card on the live page — "✓ Clean. No conflicts." beside "Blocked. This merge would silently drop 3 links, 4 properties", the `LINK LOST` badge on `payments-api/service.yaml`. Artifact: [`docs/hero.gif`](hero.gif) frame 1. | "Git calls this merge clean. The agent memory it touched is not." |
| 0:12-0:20 | Zoom the explorer graph (`auditor-agent` card): dashed red edges out of `payments-api--database--postgres-15`, panel reading "Merge blocked: 2 problems — This merge would silently drop 3 links, 4 properties". Artifact: hero.gif frame 2. | "The fact still exists — but every link to the evidence it cites is gone, and git never noticed." |
| 0:20-0:34 | GitHub PR [#15](https://github.com/himanshu748/memory-pr/pull/15): the `memory-review` comment — "Memory review: blocked (1 problem) — Contradicts a fact already in memory", the SUPERSEDES fix line, and the `mempr with Relate` footer. Artifact: hero.gif frame 3, or film the live comment. | "This is the check posting on a real pull request. The comment is a graph answer: two active values for one database, and the one-line fix — mark the new value SUPERSEDES the old." |
| 0:34-0:54 | The A/B run. Terminal: `mempr recall payments-api` → `postgres-15` on run A; `mongodb-8` on run B. Then the two PR diffs side by side: [`agent/run-a`](https://github.com/himanshu748/memory-pr-ab-fixture/pull/1) adds `postgres:15`; [`agent/run-b`](https://github.com/himanshu748/memory-pr-ab-fixture/pull/2) adds `mongo:8`. | "Same fixture, same prompt, same model — only the memory differs. The stale checkout ships Postgres, the reviewed checkout ships Mongo. Run A's memory proposal is blocked: no source, and a link to an agent note that doesn't exist." |
| 0:54-1:04 | Run A's blocked comment on PR #1 ("blocked (2 problems): links to a note that does not exist; fact has no source") beside run B's "passed" comment on PR #2. | "The review catches what the stale memory let through — and clears the reviewed one." |
| 1:04-1:16 | Quick tour of the feature map: the Cypher rule file (`default_rules.yaml`), the `DERIVED_FROM*1..6` query, then the in-page Cypher console on the live site. | "Every rule is Cypher over the Markdown vault — the same Relate engine in CI, in a serverless backend, and in this page." |
| 1:16-1:26 | `docs/real-repos.json` scrolling, then `docs/benchmarks.md` table; the word "simulated" visible on the benchmark section. | "On fifty real repositories, 154 of 328 facts an agent learned a year ago are wrong today — all blocked until superseded. The three-hundred-PR replay is labeled simulated; the A/B run is real." |
| 1:26-1:30 | Terminal, verbatim: `./quickstart.sh` → `serving http://localhost:8899`; the graph page opens. End card: repo URL. | "One command from a fresh clone. Try it." |

Production notes:

- Keep each beat on screen at least 3 s; the 0:04-0:12 and 0:20-0:34 shots are the
  payload — do not shorten them.
- If filming fresh captures is easier than embedding `docs/hero.gif`, shoot the same
  three states on <https://memory-pr.vercel.app/> (hero card → `auditor-agent`
  explorer card → PR #15 comment).
- The A/B section films from the two PR pages; both are public on
  `himanshu748/memory-pr-ab-fixture`.

# 90-second video script

[Back to README](../README.md)

Shot list. Narration in quotes; actions in plain text. Total 90 s.
Film the local page (`./quickstart.sh`) after this branch is deployed, or the live
page once Vercel has the same build. Pull request
[#60](https://github.com/himanshu748/memory-pr/pull/60) is unchanged: git still calls
that merge clean, and the review still blocks it for dropping links.

| Time | Shot | Narration |
| --- | --- | --- |
| 0:00-0:08 | The hero card. Left check: "Clean. No conflicts." Right check: "Blocked." | "Git calls this merge clean. The memory graph is not. The fact is still in the file. Every link to its evidence is gone." |
| 0:08-0:22 | Pull request [#60](https://github.com/himanshu748/memory-pr/pull/60), the `memory-review` comment: "silently drop 3 links, 4 properties." | "This is a real pull request. The required check blocks it. A text diff never shows those links." |
| 0:22-0:42 | The explorer card `reasoner-hop`: the path from `backup-policy database = mysql-8` back three hops to `payments-api database = postgres-15`. Open "Cypher for multi-hop-contradiction." | "This note is about a different entity, so the two values are not side by side. It was derived, three hops away, from a fact that says the opposite. The review is this Cypher query. The comment prints it." |
| 0:42-0:58 | The search box. Type `postgres`. The list shows the database facts, then the backup job, the migration tool, and the restore drill marked `follows, 2 hops`. | "Search for postgres. The restore drill never says that word. It is two hops along DERIVED_FROM, and Relate returns it. No model and no API key." |
| 0:58-1:14 | `action.yml` in the editor, then a terminal: `mempr search postgres` and `./test.sh` scrolling to passed tests. | "Any repo drops in one GitHub Action. Agents get the same graph over MCP, including search. The tests are one command, no Docker." |
| 1:14-1:30 | `./quickstart.sh`, the graph opens. End card: the repo URL, the live page, pull requests #60, #15 and #6. | "One command from a fresh clone. The merge git calls clean is the bug." |

Production notes:

- Keep 0:00-0:22 on the silent link drop. That is the proof already on pull request #60.
  Do not re-cut #60, #15 or #6, and do not merge them.
- 0:22-0:42 is the new card (`agent/reasoner-hop` in the playground). If the live site
  does not show it yet, film `./quickstart.sh`.
- The search box is under "Ask the graph yourself." `postgres` must show a connected
  fact whose value does not contain that word.
- #15 still blocks on a direct contradiction and #6 still passes. Say that only if
  the end card is on screen long enough to point at them.

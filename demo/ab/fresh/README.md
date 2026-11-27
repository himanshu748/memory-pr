# Fresh agent trial

The two fresh Codex sessions received the same local database setup prompt. Both
used the fixture pinned at `0a0bc4f875446efa487193b84a905d8c6687fc7f`.
The committed snapshots in this folder contain the memory notes each session was
given. The setup correction passed `mempr check` before the reviewed trial.

| Trial | Verbatim recall | Agent fixture commit | Decision basis |
| --- | --- | --- | --- |
| Stale | `payments-api database = postgres-15  [payments-api-demo-service-yaml; by scout]` | `f623781073eab312059964a79f11a82e22758e18` | Chose MongoDB 8 from the README and PyMongo implementation, and identified the recalled source as historical. |
| Reviewed | `payments-api database = mongodb-8  [https-github-com-himanshu748-memory-pr-blob-0a0bc4f875446efa487193b84a905d8c6687fc7f-demo-ab-fixture-src-db-py; by reviewer]` | `b132221848d5a83309361cbd874114032e7cedcf` | Chose MongoDB 8, citing the recalled source and corroborating it with local files. |

Both agents independently changed only Compose and the fixture's local environment
file. Both changes passed `mempr check` with zero memory edits. The stale
agent recovered by reading the project, so this fresh trial shows a difference
in recall and provenance without an observed difference in the chosen database.

Each proof PR carries its session's exact proposed files. Its CI service test
builds the unmodified HTTP fixture from those files and calls `/db` against a real
MongoDB container. The repository's `memory-review` workflow also runs on each
proof PR. These dedicated proof branches keep experiment changes off main.

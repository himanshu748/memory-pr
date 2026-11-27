# memory-pr

**Git calls the merge clean. The graph is not.**

Agents that share a project keep one memory: Markdown notes that Relate opens as a
graph. A pull request can look perfect in the diff and still drop a fact's links,
leave a fact attached to nothing, or contradict a fact six hops back along
`DERIVED_FROM`. `mempr check` reviews that pull request in Cypher, prints the query
on the comment, and blocks the merge.

![Git says clean; the graph loses the fact's links; the check blocks the PR](docs/hero.gif)

[Live page](https://memory-pr.vercel.app/) ·
[Demo video](https://youtu.be/z1qjJQhkVJ0) ·
[Devpost](https://devpost.com/software/memory-pr-pull-requests-for-agent-memory) ·
Built for the [Relate by Neo4j hackathon](https://relate.devpost.com/)

## Try it

No Docker, no credentials, no env vars.

```bash
./quickstart.sh
```

Python 3.10+ only. It serves the playground: write a memory, open a pull request,
watch the review block it, then search `postgres` and see the facts derived from it.
Verified from a fresh clone — output in [docs/EVIDENCE.md](docs/EVIDENCE.md).

Tests, also with nothing else installed for the JavaScript:

```bash
node --test tests/js/*.test.mjs   # no npm install
./test.sh                         # Python venv, then both suites
```

Git reports no merge conflicts for [demo PR #60](https://github.com/himanshu748/memory-pr/pull/60).
The `memory-review` check reports 3 lost links and 4 lost
properties. On `main`, the required check fails on the contradiction in
[PR #15](https://github.com/himanshu748/memory-pr/pull/15) and passes on
[PR #6](https://github.com/himanshu748/memory-pr/pull/6).

## What Relate does here

| Relate feature | The user-visible thing it enables | Implementation |
| --- | --- | --- |
| Cypher over the Markdown vault | `mempr recall` / `mempr query` / `mempr check` — every read and every review rule is a Cypher query on the git-tracked notes | [`src/mempr/default_rules.yaml`](src/mempr/default_rules.yaml), [`src/mempr/check.py`](src/mempr/check.py) |
| Variable-length `DERIVED_FROM` paths | a fact resting on a replaced fact, a contradiction 1–6 hops away, and a retracted source reached through that chain. The comment prints the Cypher | [`default_rules.yaml`](src/mempr/default_rules.yaml) |
| Keyword match plus those paths | `mempr search postgres` returns the database fact and the restore drill two hops away, which never says "postgres". No model, no key | [`src/mempr/search.py`](src/mempr/search.py), the search box on the page |
| WASM (`@relate/relate-wasm`) | the same rules plus a writable Cypher console run in the browser page — nothing leaves the tab | [`site/index.html`](site/index.html), [`site/app.js`](site/app.js) |
| Embedded Python (`relate-db`) | the CLI, the MCP server, the GitHub Action and the serverless backend all open the vault with the same engine | [`src/mempr/vault.py`](src/mempr/vault.py), [`api/`](api/) |
| MCP | agents get `recall`/`search`/`query` reads and `propose_fact`/`retract_fact`/`review` writes on the Relate-backed memory — writes land on a branch for the check, never on `main` | [`src/mempr/mcp_server.py`](src/mempr/mcp_server.py), [docs/agents.md](docs/agents.md) |

## Evidence

- **[Replay both agent trials in your browser](https://memory-pr.vercel.app/#agent-comparison).**
  In the sequential Devin trial, the stale run chose `postgres:15` and the reviewed
  run chose `mongo:8`. The first proposal failed graph review for missing provenance
  and returned HTTP 503 when the app tried to use its PostgreSQL URL. The reviewed
  proposal passed graph review. In two independent Codex runs, recall differed and
  both agents chose `mongo:8` after checking the code. Both of those proposals passed
  a live MongoDB health check in CI.
  [Devin check transcripts](docs/EVIDENCE.md#real-agent-run-ab) ·
  [Independent trial and CI runs](docs/EVIDENCE.md#fresh-independent-agent-trial)
- **50 real repositories:** 154 of the 328 facts learned a year ago are wrong today;
  the check blocks all 154 until superseded —
  [docs/real-repos.json](docs/real-repos.json), details in
  [docs/benchmarks.md](docs/benchmarks.md):

  | | |
  | --- | --- |
  | Repositories read | 50 |
  | Wrong facts blocked, then 0 once superseded | 154 |
- **300-PR replay (simulated):** wrong facts left in memory 21 → 1, zero correct PRs
  blocked — [docs/benchmark.json](docs/benchmark.json), details in
  [docs/benchmarks.md](docs/benchmarks.md).
- **288 pytest cases and 14 standalone JavaScript tests passed** on 27 Nov 2026.
  See the [test output](docs/EVIDENCE.md#test-count).

## Docs

- [How it works](docs/how-it-works.md) — the vault schema, the review rules, why a
  text diff is not enough, Relate's `directory` mode
- [Try it / backend](docs/usage.md) — the browser playground, real repositories, the
  API, and how a page draft becomes a real GitHub PR
- [Set up the GitHub Action](docs/action-setup.md) and require the memory review before merging
- [Use it from an agent](docs/agents.md) — `mempr setup`, the MCP tools, the skill,
  and the composite Action for your own repo — plus the judge's scorecard map
- [Benchmarks](docs/benchmarks.md) · [Evidence log](docs/EVIDENCE.md) ·
  [90-second video script](docs/VIDEO.md)

## Disclosures

- Built entirely during the contest (started Nov 20, 2026); no code reused from earlier projects.
- Built with AI assistance (Devin and OpenAI Codex). The evidence includes
  recorded Devin and Codex agent runs. Third-party libraries and tools include Relate (`relate-db`,
  `@relate/relate-wasm`), PyYAML, the MCP Python SDK, Cytoscape.js, the Geist and Geist
  Mono fonts, pytest, ruff.
- The sample project is fictional. The five scripted demo agents run without a
  model. The real-repository scan reads public files on request and stores nothing.
  The backend sets no cookies, has no accounts, and keeps no state between requests.
- License: [MIT](LICENSE).

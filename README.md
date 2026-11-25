# memory-pr

**A text diff can't show what a memory change breaks.**

![A clean Git merge drops graph links and fails memory review](docs/hero.gif)

Agents share a graph of facts and sources stored as Markdown in Git. The PR check
blocks conflicting facts and catches links that would disappear in a clean merge.
See the [blocked PR in the GIF](https://github.com/himanshu748/memory-pr/pull/60).

## How it uses Relate

Relate opens the Markdown vault as a graph. Cypher checks facts and follows
`DERIVED_FROM` paths to find stale conclusions. The browser runs Relate WASM.
The CLI, CI check, and MCP server use embedded Relate for Python.

## Quickstart

From a fresh clone with Python 3.10+ and curl:

~~~sh
./quickstart.sh
~~~

The local playground lets you propose a fact and see the graph review.

[Live demo](https://memory-pr.vercel.app/) · [Video](https://youtu.be/z1qjJQhkVJ0) ·
[Devpost](https://devpost.com/software/memory-pr-pull-requests-for-agent-memory) · [MIT](LICENSE)

[Architecture](docs/how-it-works.md) · [Setup and API](docs/usage.md) ·
[MCP and CI](docs/agents.md) · [Evidence and test results](docs/EVIDENCE.md) ·
[Benchmarks](docs/benchmarks.md) · [Video script](docs/VIDEO.md) · [Disclosures](docs/disclosures.md)

Built entirely during the contest (started Nov 20, 2026); no code reused from earlier projects.

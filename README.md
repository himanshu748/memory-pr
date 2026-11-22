# memory-pr: pull requests for agent memory

Agents that work on the same project keep rediscovering the same things, and when they
do share notes, one wrong note quietly becomes everyone's truth.

memory-pr gives a team of agents one shared memory that is reviewed like code:

- The memory is a **graph stored as Markdown files in git** (folders are labels, notes
  are nodes, wikilinks are relationships). [Relate](https://relatedb.ai) opens that
  folder as a database, so agents read and write it in Cypher.
- Every change an agent makes arrives as a **pull request**.
- A check reads the pull request **as graph facts, not as text**, and comments what it
  adds, what it contradicts, and what the merge would silently destroy. A bad change
  cannot be merged.

Built for the [Relate by Neo4j hackathon](https://relate.devpost.com/).

## How it uses Relate

- **Mode: directory.** Relate opens the [`memory/`](memory) folder as a Markdown vault
  (not in memory, not a single file).
- **Why this mode:** the git-tracked Markdown notes are the database. Because the store
  is plain files in the repository, a change to the graph is a commit, and a pull request
  can review the graph before it merges. An in-memory graph has no history to review, and
  a single file would not give git one note per fact to diff and merge.
- **What the graph models:** what a team of agents knows about a project. A `Fact` is
  `ABOUT` an `Entity`, `ASSERTED_BY` an `Agent`, backed by a `SOURCE`, and can
  `SUPERSEDES` or be `DERIVED_FROM` another fact. Those relationships are what the
  review checks. The full shape is under [The graph](#the-graph).
- **Where the data lives:** [`memory/`](memory), one folder per label and one note per
  node, committed in this repository. The browser demo loads the same graph into
  Relate's WebAssembly build, in memory in the page.

## Why a text diff is not enough

Git merges text. A memory graph can be damaged by a merge git calls clean:

| What happened | Git says | The graph says |
| --- | --- | --- |
| Two agents record different databases for the same service, in two new notes | clean merge | the service now has two active values for one fact |
| An agent replaces a fact that three other facts were derived from | clean merge | three facts rest on a fact that is no longer true, up to two hops away |
| Two branches add the same property to one note on different lines | clean merge | the note has a duplicate key, Relate reads it as empty, and every link it had is gone |
| An agent links a fact to a source that was never written down | clean merge | the fact cannot be verified |

Each row is a Cypher query or a graph comparison in this repository, and each has a
test.

## Try it in two minutes

1. **Nothing to install:** open <https://memory-pr.vercel.app/>, press a starter under
   "Write a memory", then "Open pull request". The review blocks it, the fixes are one
   click each, and a passing pull request merges into the page's memory.
2. **On GitHub:** two pull requests a scripted agent opened in this repository.
   [#15](https://github.com/himanshu748/memory-pr/pull/15) contradicts the memory, so
   `memory-review` commented and blocks the merge.
   [#6](https://github.com/himanshu748/memory-pr/pull/6) passes.
3. **On your machine:** clone, `pip install -e ".[dev]"`, then `pytest -q` and
   `mempr recall payments-api`. The full commands are under
   [Run it locally](#run-it-locally).

With write access (your own fork, for example) you can also open a new one: **Actions →
demo-agent → Run workflow**, pick a scenario (`contradiction`, `supersede`,
`stale-clone`, `unsourced` or `clean`), and a scripted agent writes to the memory on a
new branch and opens a pull request for `memory-review` to check.

## See it in the browser

**<https://memory-pr.vercel.app/>** (also at <https://himanshu748.github.io/memory-pr/>). Nothing to install.

![The browser view: a pull request that contradicts memory, drawn on the graph](docs/browser-view.png)

- **Write a memory yourself.** Fill in a short form as an agent, see the Markdown note it becomes,
  and open a pull request against a copy of the memory in the page. The real rules review it, the
  fixes are one click, and a passing pull request merges into the page's memory. The same drafts are
  reviewed by `mempr check` in
  [`tests/test_playground_cases.py`](tests/test_playground_cases.py), so the page and the command
  line agree.
- **The shared memory as a graph.** Click any fact for the file it came from, the agent that wrote
  it, what it replaces and what it rests on, and the Markdown note behind it.
- **What a pull request does to it.** Five scripted pull requests, one per card: the facts each adds
  (green) and loses (red, dashed), and every problem the review found drawn on the graph, such as
  the two contradicting facts and the path between them, or the chain of derived facts resting on a
  replaced one.
- **Relate runs in the page.** The graph is loaded into Relate's WebAssembly build
  ([`@relate/relate-wasm`](https://www.npmjs.com/package/@relate/relate-wasm)), so the Cypher box
  queries it live, the review rules run in your browser, and you can write to the graph (retract a
  fact, add a contradicting one) and watch the review change. Nothing leaves the page.

The site is static with no build step and works offline: `python -m http.server -d site`, then open
<http://localhost:8000>. Its data, [`site/data/demo.json`](site/data/demo.json), comes from
`mempr export` (the graph and each review as JSON, from `mempr.vault` and `mempr.check`) and is
regenerated with `python demo/build_site.py`.

An example that is already open: [pull request 15](https://github.com/himanshu748/memory-pr/pull/15),
where the agent read an outdated design note.

Two repository settings make this work, both set once by the owner:

- **Settings → Actions → General → Workflow permissions**: tick "Allow GitHub Actions to
  create and approve pull requests", so the agent workflow can open its pull request.
- **Settings → Branches**: protect `main` and require the `memory-review` status check,
  so a blocked pull request cannot be merged.

## Measured result

300 pull requests from simulated agents (5 seeds, 10 rounds, 6 agents per round,
30% of pull requests carry one of four mistakes), replayed with and without the check.
At the end a new agent asks the memory about every service.

| | Without the check | With the check |
| --- | --- | --- |
| Pull requests merged | 299 | 255 |
| Pull requests blocked | 0 | 45 |
| Pull requests fixed after the review, then merged | 0 | 8 |
| Correct pull requests blocked by mistake | 0 | 0 |
| Wrong facts in memory at the end | 20 | 1 |
| Contradictions in memory | 24 | 0 |
| Facts resting on a replaced fact | 9 | 0 |
| Notes emptied by a merge git called clean | 3 | 0 |
| Facts with no source | 20 | 0 |
| Questions answered correctly (of 240) | 140 | 160 |
| Questions answered wrongly or with two values | 18 | 1 |

Reproduce: `python bench/run.py --seed 1 --seeds 5 --rounds 10 --agents 6 --out docs/benchmark.json`
(about 2 minutes). The agents, the project and its ground truth are generated in
[`bench/run.py`](bench/run.py); raw numbers are in [`docs/benchmark.json`](docs/benchmark.json).
The questions left unanswered in both columns are facts no agent was asked to learn.
The one wrong fact that remains with the check is a true change whose pull request
stayed blocked, so the memory still holds the older value.

## Run it locally

Requires Python 3.10+ and git 2.38+.

```bash
git clone https://github.com/himanshu748/memory-pr && cd memory-pr
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q

# Ask the memory something (current facts only, each with its source)
mempr recall payments-api

# Any Cypher, straight on the Markdown files
mempr query "MATCH p = (f:Fact)-[:DERIVED_FROM*]->(r:Fact {name: 'payments-api--database--postgres-15'}) RETURN f.name AS fact, length(p) AS hops"

# Let an agent make a bad change on a branch, then review it
python demo/agent.py run --scenario contradiction
mempr check --base origin/main --head HEAD
```

### Inspect the store with Relate directly

The store is the `memory/` folder. With the [Relate CLI](https://relatedb.ai):

```bash
relate --snapshot-format markdown --database memory \
  "MATCH (f:Fact)-[:ABOUT]->(e:Entity) RETURN e.name, f.predicate, f.value"
```

Relate writes its own state to `memory/.relate/` and regenerates `index.md` files; both
are git-ignored because the notes are the durable store.

## The graph

```
(:Fact {predicate, value, status})-[:ABOUT]->(:Entity)
(:Fact)-[:ASSERTED_BY]->(:Agent)
(:Fact)-[:SOURCE]->(:Source {uri})
(:Fact)-[:SUPERSEDES]->(:Fact)
(:Fact)-[:DERIVED_FROM]->(:Fact)
(:Predicate {cardinality})          # optional: `many` allows several values
```

A fact is one note, for example `memory/Fact/payments-api--database--postgres-15.md`:

```markdown
---
type: Fact
name: payments-api--database--postgres-15
predicate: database
status: active
value: postgres-15
ABOUT:
  - "[[Entity/payments-api]]"
ASSERTED_BY:
  - "[[Agent/scout]]"
SOURCE:
  - "[[Source/demo-project-services-payments-api-service-yaml]]"
---

payments-api database: postgres-15
```

## What the check does

`mempr check --base <branch> --head <revision>`:

1. Loads four versions of the graph with Relate: the common ancestor, the base branch,
   the pull request, and the result of git's merge (`git merge-tree`, nothing is checked
   out).
2. Reports the pull request as added and removed graph facts.
3. Merges the two graphs itself and compares that with git's merge. A fact that neither
   side removed but that is missing after the merge is reported as dropped. A property
   both sides set to different values is reported as a conflict.
4. Runs the review rules, which are Cypher queries, on the merged graph and reports only
   the findings the pull request introduces.
5. Exits 1 when anything blocks, so a required status check stops the merge; exits 2
   when the review itself cannot run (for example, no common ancestor between the
   branches).

Rules live in [`src/mempr/default_rules.yaml`](src/mempr/default_rules.yaml). Add your
own in `memory-rules.yaml` at the repository root; a rule is an id, a severity (`block`
or `warn`), a message template, and a Cypher query whose rows are the findings. The
check reads that file from the base branch, so a pull request cannot change the rules
it is reviewed with.

| Rule | Blocks | Finds |
| --- | --- | --- |
| `contradiction` | yes | two current facts giving one entity different values for one predicate |
| `stale-derivation` | yes | an active fact derived, up to 6 hops away, from a retracted or replaced fact |
| `supersedes-cycle` | yes | facts that replace each other in a circle, so none is current |
| `dangling-link` | yes | a link to a note that does not exist |
| `unsourced-fact` | yes | an active fact with no source |
| `unattributed-fact` | no | a fact with no author |

## Use it from an agent

`mempr mcp` is an [MCP](https://modelcontextprotocol.io) server over stdio, so Claude Code,
Cursor, Devin or any other MCP client can use the memory while it works. Add it to the
client's MCP config (`.mcp.json` for Claude Code, `.cursor/mcp.json` for Cursor), with the
path of the repository that holds `memory/`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/himanshu748/memory-pr",
        "mempr", "--repo", "/absolute/path/to/your/repo", "mcp"
      ]
    }
  }
}
```

With `mempr` already installed, the command is `mempr --repo /absolute/path/to/your/repo mcp`
(Claude Code: `claude mcp add memory -- mempr --repo "$PWD" mcp`).

| Tool | What the agent gets |
| --- | --- |
| `recall` | The current facts about an entity, each with its sources and authors. For an entity the memory does not know, the answer is "Nothing known ... Do not guess", not an empty list to fill in. |
| `query` | The rows of a read-only Cypher query. Writing clauses are refused, and the query runs on a throwaway copy of the files. |
| `propose_fact` | One fact, written with `mempr.memory.add_fact` and committed on a new branch, plus the review of that branch. `open_pull_request: true` also pushes it and opens the pull request with `gh`. |
| `retract_fact` | The same for a retraction. |
| `review` | The review of any branch against a base: the result of `mempr check` as JSON, and the comment text. |

An agent cannot write to the memory directly. A proposal is committed on an `agent/*`
branch in a temporary git worktree, so the branch you have checked out, your working tree and `main` do not move.
When the review blocks a proposal, the agent reads why and sends the fix with the same
`branch`; the fact becomes shared memory only when the pull request is merged.
`--base` sets the branch proposals start from and are reviewed against (default
`origin/main`).

## Use it in your own repository

1. Either copy `.github/workflows/memory-review.yml` and install `mempr` in it from this
   repository (`pip install git+https://github.com/himanshu748/memory-pr`), or use the
   bundled action once a release tag exists:

   ```yaml
   - uses: actions/checkout@v4
     with:
       fetch-depth: 0
   - uses: himanshu748/memory-pr@v1
     with:
       base: origin/main
       head: ${{ github.event.pull_request.head.sha }}
   ```
2. Keep your memory under `memory/` (or pass `--vault`).
3. In the branch protection for your main branch, require the `memory-review` check.

## How Relate is used

- **Markdown vault store**: the git-tracked notes are the database. There is no import
  step and no second copy of the data.
- **Cypher** for every read and write: recall, fact writes (`MERGE`), all review rules,
  including the variable-length `DERIVED_FROM*1..6` path query.
- **Embedded Python library** (`relate-db`), inside the CLI, the MCP server and the GitHub
  Action.
- **WebAssembly build** (`@relate/relate-wasm`) in the browser view, running the same review
  rules on the same graph in the page.

## Data and disclosures

- All data is generated. The sample project under `demo/project/` and the memory under
  `memory/` are fictional. Nothing was recorded; there is no voice or video.
- The demo agents are scripted and call no model.
- Built with AI assistance (Devin). Third-party: Relate (`relate-db`, `@relate/relate-wasm`),
  PyYAML, the MCP Python SDK, Cytoscape.js (graph drawing in the browser view), the Geist and
  Geist Mono fonts (self-hosted, SIL Open Font License), pytest, ruff. The browser view is a
  static page hosted on Vercel and GitHub Pages.

## License

MIT

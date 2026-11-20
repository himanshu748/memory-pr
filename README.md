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

1. Open **Actions → demo-agent → Run workflow** in this repository and pick a scenario
   (`contradiction`, `supersede`, `stale-clone`, `unsourced` or `clean`).
2. A scripted agent writes to the memory on a new branch and opens a pull request.
3. The **memory-review** check comments on the pull request and, unless the scenario is
   `clean`, blocks the merge.

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
5. Exits 1 when anything blocks, so a required status check stops the merge.

Rules live in [`src/mempr/default_rules.yaml`](src/mempr/default_rules.yaml). Add your
own in `memory-rules.yaml` at the repository root; a rule is an id, a severity (`block`
or `warn`), a message template, and a Cypher query whose rows are the findings.

| Rule | Blocks | Finds |
| --- | --- | --- |
| `contradiction` | yes | two active facts giving one entity different values for one predicate |
| `stale-derivation` | yes | an active fact derived, up to 6 hops away, from a retracted or replaced fact |
| `dangling-link` | yes | a link to a note that does not exist |
| `unsourced-fact` | yes | an active fact with no source |
| `unattributed-fact` | no | a fact with no author |

## Use it in your own repository

1. Copy `.github/workflows/memory-review.yml` and install `mempr` in it from this
   repository (`pip install git+https://github.com/himanshu748/memory-pr`).
2. Keep your memory under `memory/` (or pass `--vault`).
3. In the branch protection for your main branch, require the `memory-review` check.

## How Relate is used

- **Markdown vault store**: the git-tracked notes are the database. There is no import
  step and no second copy of the data.
- **Cypher** for every read and write: recall, fact writes (`MERGE`), all review rules,
  including the variable-length `DERIVED_FROM*1..6` path query.
- **Embedded Python library** (`relate-db`), inside the CLI and the GitHub Action.

## Data and disclosures

- All data is generated. The sample project under `demo/project/` and the memory under
  `memory/` are fictional. Nothing was recorded; there is no voice or video.
- The demo agents are scripted and call no model.
- Built with AI assistance (Devin). Third-party: Relate (`relate-db`), PyYAML, pytest,
  ruff.

## License

MIT

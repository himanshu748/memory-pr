# How memory-pr works

[Back to README](../README.md)

## How it uses Relate

- **Mode: directory.** Relate opens the [`memory/`](../memory) folder as a Markdown vault
  (not in memory, not a single file).
- **Why this mode:** the git-tracked Markdown notes are the database. Because the store
  is plain files in the repository, a change to the graph is a commit, and a pull request
  can review the graph before it merges. An in-memory graph has no history to review, and
  a single file would not give git one note per fact to diff and merge.
- **What the graph models:** what a team of agents knows about a project. A `Fact` is
  `ABOUT` an `Entity`, `ASSERTED_BY` an `Agent`, backed by a `SOURCE`, and can
  `SUPERSEDES` or be `DERIVED_FROM` another fact. Those relationships are what the
  review checks. The full shape is under [The graph](#the-graph).
- **Where the data lives:** [`memory/`](../memory), one folder per label and one note per
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
| A new note about a different entity is derived, three hops away, from a fact that states the opposite | clean merge | the conclusion contradicts the fact it came from |
| A note names an entity in its file name and forgets the `ABOUT` link | clean merge | recall never returns the fact |
| A fact cites a source, or was derived from a fact that does, and that source was retracted | clean merge | the evidence is gone and the conclusion is still current |

Each row is a Cypher query or a graph comparison in this repository, and each has a
test.

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

Rules live in [`src/mempr/default_rules.yaml`](../src/mempr/default_rules.yaml). Add your
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
| `unsourced-fact` | yes | an active fact with no `SOURCE` link to a `Source` with a URI |
| `unattributed-fact` | no | a fact with no author |
| `incomplete-fact` | yes | a fact missing its predicate, value, or supported status |
| `unrelated-replacement` | yes | a replacement of a note outside the fact's entity and predicate |
| `orphan-fact` | yes | an active fact with no `ABOUT` link, so recall cannot find it |
| `multi-hop-contradiction` | yes | a derivation marked `preserve_value: true` that changes the ancestor's value for the same predicate, up to 6 hops away, unless the predicate's cardinality is `many` |
| `broken-provenance` | yes | an active fact that reaches a retracted source, directly or through up to 6 `DERIVED_FROM` hops |

The check comment includes the Cypher for each rule that fired, in a collapsed block under the problems. The same queries run in CI, in the API, and in the page.

`DERIVED_FROM` records a dependency. A transformed value such as a shorter
health-probe timeout can depend on the service's request timeout. When the
derivation is meant to copy the value, pass `--preserve-value` to `mempr add-fact`
or set `preserve_value: true` in the fact note or MCP proposal. The copy is then
checked against its same-predicate ancestors.

## Inspect the store with Relate directly

The store is the `memory/` folder. With the [Relate CLI](https://relatedb.ai):

```bash
relate --snapshot-format markdown --database memory \
  "MATCH (f:Fact)-[:ABOUT]->(e:Entity) RETURN e.name, f.predicate, f.value"
```

Relate writes its own state to `memory/.relate/` and regenerates `index.md` files; both
are git-ignored because the notes are the durable store.

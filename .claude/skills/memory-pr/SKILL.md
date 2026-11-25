---
name: memory-pr
description: >-
  Use the project's shared memory (the Markdown notes under memory/, a graph read with
  Relate) and change it only by pull request. Use before assuming a fact about the
  project (which database, owner, runtime or tool a service uses), and whenever you
  learn, correct or retract such a fact.
---

# Shared memory, changed by pull request

`memory/` is a graph of facts kept as Markdown and reviewed like code. One note is one
fact: `(:Fact {predicate, value, status})-[:ABOUT]->(:Entity)`, with `SOURCE`,
`ASSERTED_BY`, `SUPERSEDES` and `DERIVED_FROM` links.

Rules:

- Never edit `memory/` on the main branch, and never write a fact you did not read
  somewhere. Every fact needs the file path or URL it came from.
- Recall before you assume. If nothing is known, read the real source, then propose.
- A fact is shared memory only after its pull request is merged. The `memory-review`
  check decides that, not you.

## Recall

With the `memory` MCP server: `recall(entity)` when you know the entity, or `search(text)`
when you only have a word. `query(cypher)` for anything else.
Without it:

```bash
mempr recall payments-api
mempr search postgres
mempr query "MATCH (f:Fact {status:'active'})-[:ABOUT]->(e:Entity) RETURN e.name, f.predicate, f.value"
```

## Propose a fact

With the MCP server, call `propose_fact` (or `retract_fact`) with
`open_pull_request: true`. It commits on a new `agent/*` branch, opens the pull request
and returns the review. Calling it again with the same fact fails with "nothing to
change", so set the flag on the call that makes the change.

Without it:

```bash
git switch -c agent/<short-name> origin/main
mempr add-fact --entity payments-api --predicate database --value mysql-8 \
  --agent <your-name> --source docs/design.md
git add memory && git commit -m "memory: payments-api database mysql-8"
mempr check --base origin/main        # exit 1 means the merge is blocked
git push -u origin HEAD && gh pr create --fill
```

## When the review blocks

Read each problem, fix it on the same branch (MCP: pass the same `branch`), check again.

| Problem | Fix |
| --- | --- |
| Contradicts a fact already in memory | If yours replaces it, add `--supersedes <old fact name>`. If the old one is right, drop yours. |
| Rests on a fact that was retracted or replaced | Re-check each derived fact against the new value. Still true in a new form: add the new fact with `--supersedes <derived fact> --derived-from <new fact>`. No longer true: `mempr retract <derived fact>`. |
| No source | Add the fact again with `--source`. |
| Link to a note that does not exist | Use the fact name exactly as `recall` prints it. |
| Contradicts a fact it was derived from | The path in the review names the ancestor. Point `DERIVED_FROM` at the fact that states the value you used, or drop yours. |
| Fact is not about anything | Add `ABOUT` for the entity. A file name is not a link. |
| Evidence for this fact was retracted | Cite a source that is still current, or retract the fact. |

Fact names look like `payments-api--database--postgres-15`. Do not retract a fact only
to make the check pass: retract it when you have checked it is no longer true.

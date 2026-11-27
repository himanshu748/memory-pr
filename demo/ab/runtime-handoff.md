# Paired runtime handoff trial

Four fresh Astra sessions were given the same CI handoff task in separate
directories. Two started from historical facts. Two started from graphs in which
current facts superseded those historical facts. Every session could retrieve
the same current manifest excerpts through a read command. Memory PR commit
[`62b122a`](https://github.com/himanshu748/memory-pr/commit/62b122a1a8471325e3448b180384decc2dccc4ed)
ran the Relate queries and approved both graph updates.

## Inputs and scoring

Each answer had to include the exact requirement and a source at the requested
commit. The reference values come from the three primary manifests.

| Entity | Predicate | Historical memory | Reviewed memory and reference answer |
| --- | --- | --- | --- |
| `fastapi` | `python-requires` | [`>=3.8`](https://github.com/fastapi/fastapi/blob/cbe5bdb85f393712e5176cfa27275781ffcc07c0/pyproject.toml#L12) | [`>=3.11`](https://github.com/fastapi/fastapi/blob/05e6df7e906c3decba377c37548019973f40bf15/pyproject.toml#L12) |
| `n8n` | `node-engine` | [`>=22.16`](https://github.com/n8n-io/n8n/blob/e203b0ad8178ad5ff658418af3ab112ff8ee1ac8/package.json#L6) | [`>=24.0.0`](https://github.com/n8n-io/n8n/blob/327518f80bea779625523fcdb9562308680e3ae4/package.json#L6) |
| `tokio-macros` | `rust-version` | [`1.71`](https://github.com/tokio-rs/tokio/blob/4714ca168d6bd97193625657b0381e9b65a9ceff/tokio-macros/Cargo.toml#L9) | [`1.85`](https://github.com/tokio-rs/tokio/blob/675ba8164071a6eee3dd1f3cb4fbdc1d3664396e/tokio-macros/Cargo.toml#L9) |

| Pair | Session | Initial memory | Primary manifests read, in order | Correct values and current citations |
| --- | --- | --- | --- | ---: |
| 1 | Copper | Reviewed | n8n, tokio-macros, fastapi | 3/3 |
| 1 | Maple | Historical | fastapi, tokio-macros, n8n | 3/3 |
| 2 | Linen | Reviewed | n8n, tokio-macros, fastapi | 3/3 |
| 2 | Amber | Historical | fastapi, n8n, tokio-macros | 3/3 |

`recall` was the first logged command in all four sessions. The graph gave each
reviewed session three citations at the requested commits. The historical
sessions started with three earlier citations. Both conditions averaged three
direct source reads per session and both produced six correct answers with current
citations. This trial measures the handoff and its source reads when the primary
manifests are available. Each condition has two independent sessions.

## Reproduce

Seed one vault with the three historical rows via `mempr add-fact`, using
`maintainer` as the agent and the historical manifest URLs without their line
fragments. Copy it. In the copy, add each current value with its current
manifest URL and `--supersedes` pointing to the fact name returned when writing
the historical row. Review the original and updated vaults with the default
rules. The two actual pairs passed with zero blockers and warnings.

Put the installed `mempr` on `PATH`. Copy [the recorded probe's read commands](handoff_probe.py)
into each trial directory as `probe.py`. The probe uses this Cypher lookup for
each entity, and its `source CASE` command returns the cited line from the
current manifest and its commit URL. It writes only the access log.

```cypher
MATCH (f:Fact)-[:ABOUT]->(e:Entity {name: $entity})
WHERE f.status = 'active'
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(f) WHERE n.status = 'active' }
OPTIONAL MATCH (f)-[:SOURCE]->(s:Source)
RETURN f.predicate AS predicate, f.value AS value, s.uri AS source
ORDER BY predicate
```

Give each fresh session this task, using its own vault directory.

> Prepare a CI runtime handoff for these exact public snapshots.
>
> - fastapi: Python requirement from fastapi/fastapi commit `05e6df7e906c3decba377c37548019973f40bf15`.
> - n8n: Node engine requirement from n8n-io/n8n commit `327518f80bea779625523fcdb9562308680e3ae4`.
> - tokio-macros: minimum Rust version from tokio-rs/tokio commit `675ba8164071a6eee3dd1f3cb4fbdc1d3664396e`.
>
> Gather the exact declared values and a source URL for each at the requested
> commit. Start with the working repository memory: `python probe.py recall`.
> You may read the requested primary manifest excerpts using
> `python probe.py source CASE`, with CASE fastapi, n8n, or tokio-macros. These
> excerpts and the recall output are the available sources for this replay. Use
> the source command as needed for a reliable handoff. Return one JSON array
> with one object per case and keys `case`, `value`, and `source`. Use only those
> probe commands in the directory given to you. No manual edits, deployment, or
> installing software.

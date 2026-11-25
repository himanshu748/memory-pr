"""Hybrid retrieval: a keyword match, then the facts up to six hops along DERIVED_FROM.

No model and no API key. The keyword half reads entity names, predicates, values,
statements and source paths. The graph half walks `DERIVED_FROM` so a search for
`postgres` also returns the backup job and the restore drill that never say that word.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .vault import open_vault, rows

MIN_QUERY = 2
LIMIT = 40

# Current facts whose text or source path contains $q.
LEXICAL = """
MATCH (hit:Fact)-[:ABOUT]->(e:Entity)
WHERE hit.status = 'active'
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(hit) WHERE n.status = 'active' }
OPTIONAL MATCH (hit)-[:SOURCE]->(src:Source)
WITH hit, e, collect(DISTINCT src.uri) AS sources
WHERE toLower(e.name) CONTAINS toLower($q)
   OR toLower(hit.predicate) CONTAINS toLower($q)
   OR toLower(coalesce(hit.value, '')) CONTAINS toLower($q)
   OR toLower(coalesce(hit.body, '')) CONTAINS toLower($q)
   OR any(u IN sources WHERE toLower(coalesce(u, '')) CONTAINS toLower($q))
RETURN hit.name AS fact, e.name AS entity, hit.predicate AS predicate,
       hit.value AS value, sources
ORDER BY entity, predicate, value
""".strip()

# Facts derived from a lexical hit, and the facts a hit was itself derived from.
# $names is the `fact` column of LEXICAL. Hits are left out. Each direction
# returns one row per fact, with the shortest distance across all matching paths.
NEIGHBORHOOD = """
MATCH (hit:Fact) WHERE hit.name IN $names
MATCH p = (child:Fact)-[:DERIVED_FROM*1..6]->(hit)
WHERE child.status = 'active'
  AND NOT child.name IN $names
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(child) WHERE n.status = 'active' }
OPTIONAL MATCH (child)-[:ABOUT]->(e:Entity)
RETURN child.name AS fact, min(e.name) AS entity, child.predicate AS predicate,
       child.value AS value, min(length(p)) AS hops, 'follows' AS via
UNION
MATCH (hit:Fact) WHERE hit.name IN $names
MATCH p = (hit)-[:DERIVED_FROM*1..6]->(root:Fact)
WHERE root.status = 'active'
  AND NOT root.name IN $names
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(root) WHERE n.status = 'active' }
OPTIONAL MATCH (root)-[:ABOUT]->(e:Entity)
RETURN root.name AS fact, min(e.name) AS entity, root.predicate AS predicate,
       root.value AS value, min(length(p)) AS hops, 'supports' AS via
""".strip()


def search(vault: Path, text: str) -> dict[str, Any]:
    """Matches for `text`, plus facts reached from them along `DERIVED_FROM`."""
    query = " ".join(text.split())
    found: dict[str, Any] = {
        "query": query,
        "cypher": {"lexical": LEXICAL, "neighborhood": NEIGHBORHOOD},
        "hits": [],
        "connected": [],
    }
    if len(query) < MIN_QUERY:
        found["note"] = f"the query needs at least {MIN_QUERY} characters"
        return found
    with open_vault(vault) as conn:
        hits = rows(conn, LEXICAL, {"q": query})
        names = [hit["fact"] for hit in hits]
        connected = rows(conn, NEIGHBORHOOD, {"names": names}) if names else []
    connected.sort(key=lambda row: (row["via"], row["hops"], row["entity"] or "", row["fact"]))
    found["hits"] = hits[:LIMIT]
    found["connected"] = connected[:LIMIT]
    found["truncated"] = len(hits) > LIMIT or len(connected) > LIMIT
    return found


def format_search(result: dict[str, Any]) -> str:
    """The search as plain text. The Cypher is in the JSON (`mempr search --json`)."""
    if result.get("note"):
        return result["note"]
    hits, connected = result["hits"], result["connected"]
    lines = [
        f"{len(hits)} match(es), {len(connected)} connected fact(s) for {result['query']!r}.",
    ]
    for hit in hits:
        sources = ", ".join(uri for uri in hit["sources"] if uri) or "no source"
        lines.append(
            f"  match     {hit['entity']} {hit['predicate']} = {hit['value']}  [{sources}]"
        )
    for row in connected:
        entity = row["entity"] or "(no entity)"
        lines.append(
            f"  {row['via']:<9} {entity} {row['predicate']} = {row['value']}"
            f"  ({row['hops']} hop(s))"
        )
    if not hits:
        lines.append("  Nothing matches. Try another word, or recall an entity by name.")
    lines.append("The Cypher is in `mempr search --json`.")
    return "\n".join(lines)

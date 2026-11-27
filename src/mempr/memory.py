"""What an agent does with the shared memory: recall facts and write new ones, in Cypher."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .vault import open_vault, rows


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fact_name(entity: str, predicate: str, value: str) -> str:
    """The note name a fact gets when no other value already holds that name."""
    return f"{slug(entity)}--{slug(predicate)}--{slug(value)}"


def _free_name(conn, entity: str, predicate: str, value: str) -> str:
    """The fact's own note, or a name no other value holds (`C++` and `C#` both slug to `c`)."""
    base = fact_name(entity, predicate, value)
    names = (base, f"{base}-{hashlib.sha1(value.encode()).hexdigest()[:6]}")
    held = {
        r["name"]: r["value"]
        for r in rows(
            conn,
            "MATCH (f:Fact) WHERE f.name IN $names RETURN f.name AS name, f.value AS value",
            {"names": list(names)},
        )
    }
    for name in names:  # the note that already states this value, wherever it is
        if name in held and str(held[name]) == value:
            return name
    for name in names:
        if held.get(name) is None:
            return name
    raise ValueError(f"no free note name for {entity} {predicate} {value!r}")


RECALL = """
MATCH (f:Fact)-[:ABOUT]->(e:Entity {name: $entity})
WHERE f.status = 'active'
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(f) WHERE n.status = 'active' }
OPTIONAL MATCH (f)-[:SOURCE]->(s:Source)
OPTIONAL MATCH (f)-[:ASSERTED_BY]->(a:Agent)
RETURN f.name AS fact, f.predicate AS predicate, f.value AS value, f.body AS statement,
       collect(DISTINCT s.name) AS sources, collect(DISTINCT a.name) AS agents
ORDER BY predicate, value
"""


def recall(vault: Path, entity: str) -> list[dict[str, Any]]:
    """Current facts about an entity, each with the sources and agents behind it."""
    with open_vault(vault) as conn:
        return rows(conn, RECALL, {"entity": entity})


ADD_FACT = """
MERGE (e:Entity {name: $entity})
MERGE (a:Agent {name: $agent})
MERGE (s:Source {name: $source})
SET s.uri = $uri
MERGE (f:Fact {name: $name})
SET f.predicate = $predicate, f.value = $value, f.status = 'active', f.body = $statement
MERGE (f)-[:ABOUT]->(e)
MERGE (f)-[:ASSERTED_BY]->(a)
MERGE (f)-[:SOURCE]->(s)
RETURN f.name AS fact
"""

# A fact written with no source, as a hand-written note can be. The review blocks it.
ADD_UNSOURCED_FACT = ADD_FACT.replace(
    "MERGE (s:Source {name: $source})\nSET s.uri = $uri\n", ""
).replace("MERGE (f)-[:SOURCE]->(s)\n", "")

LINK = {
    "SUPERSEDES": (
        "MATCH (f:Fact {name: $name}), (o:Fact {name: $other}) "
        "MERGE (f)-[:SUPERSEDES]->(o) RETURN o.name AS other"
    ),
    "DERIVED_FROM": (
        "MATCH (f:Fact {name: $name}), (o:Fact {name: $other}) "
        "MERGE (f)-[:DERIVED_FROM]->(o) RETURN o.name AS other"
    ),
}


def _check_before_write(conn, uri: str | None, linked: list[str]) -> None:
    """Refuse a write that would overwrite another source or link to a fact that is not there."""
    for other in linked:
        if not rows(conn, "MATCH (f:Fact {name: $name}) RETURN f.name AS name", {"name": other}):
            raise KeyError(f"no fact named {other!r} to link to")
    if not uri:
        return
    taken = rows(conn, "MATCH (s:Source {name: $name}) RETURN s.uri AS uri", {"name": slug(uri)})
    if taken and taken[0]["uri"] not in (None, uri):
        raise ValueError(f"source {uri!r} has the same note name as {taken[0]['uri']!r}")


def add_fact(
    vault: Path,
    *,
    entity: str,
    predicate: str,
    value: str,
    agent: str,
    source: str | None,
    statement: str = "",
    supersedes: list[str] | None = None,
    derived_from: list[str] | None = None,
    preserve_value: bool = False,
    about: bool = True,
    source_status: str | None = None,
) -> str:
    """Write one fact into the working-tree vault. Returns the fact's name.

    `source` is the file, commit or document the agent read it from. Without one the fact
    is still written, and the review blocks it. `about=False` writes the fact with no
    `ABOUT` link (the review blocks that too). `source_status='retracted'` marks the
    source note retracted. Use `preserve_value` when the derivation copies the value.
    """
    for label, text in (("entity", entity), ("predicate", predicate), ("agent", agent)):
        if slug(text) != text:
            raise ValueError(f"{label} {text!r} must be lowercase letters, digits and hyphens")
    if source_status not in (None, "retracted"):
        raise ValueError("source_status must be retracted")
    if source_status and not source:
        raise ValueError("source_status needs a source")
    vault.mkdir(parents=True, exist_ok=True)
    supersedes, derived_from = supersedes or [], derived_from or []
    query = ADD_FACT if source else ADD_UNSOURCED_FACT
    if not about:
        query = query.replace("MERGE (e:Entity {name: $entity})\n", "").replace(
            "MERGE (f)-[:ABOUT]->(e)\n", ""
        )
    with open_vault(vault, in_place=True) as conn:
        _check_before_write(conn, source, supersedes + derived_from)
        name = _free_name(conn, entity, predicate, value)
        if name in supersedes + derived_from:
            raise ValueError(f"fact {name!r} cannot replace or rest on itself")
        # Stating a fact again makes it current: nothing that replaced it still does.
        rows(conn, "MATCH (:Fact)-[r:SUPERSEDES]->(:Fact {name: $name}) DELETE r", {"name": name})
        rows(
            conn,
            query,
            {
                "entity": entity,
                "agent": agent,
                **({"source": slug(source), "uri": source} if source else {}),
                "name": name,
                "predicate": predicate,
                "value": value,
                "statement": statement or f"{entity} {predicate}: {value}",
            },
        )
        if not about:
            # Restating a fact must drop an ABOUT link it already had.
            rows(conn, "MATCH (:Fact {name: $name})-[r:ABOUT]->() DELETE r", {"name": name})
        if source_status:
            rows(
                conn,
                "MATCH (s:Source {name: $name}) SET s.status = $status RETURN s.name AS name",
                {"name": slug(source), "status": source_status},
            )
        if preserve_value:
            rows(conn, "MATCH (f:Fact {name: $name}) SET f.preserve_value = true", {"name": name})
        for other in supersedes:
            rows(conn, LINK["SUPERSEDES"], {"name": name, "other": other})
        for other in derived_from:
            rows(conn, LINK["DERIVED_FROM"], {"name": name, "other": other})
    return name


def retract(vault: Path, fact: str) -> None:
    with open_vault(vault, in_place=True) as conn:
        found = rows(
            conn,
            "MATCH (f:Fact {name: $name}) SET f.status = 'retracted' RETURN f.name AS fact",
            {"name": fact},
        )
    if not found:
        raise KeyError(f"no fact named {fact!r}")

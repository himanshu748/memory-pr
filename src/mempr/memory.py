"""What an agent does with the shared memory: recall facts and write new ones, in Cypher."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .vault import open_vault, rows


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fact_name(entity: str, predicate: str, value: str) -> str:
    return f"{slug(entity)}--{slug(predicate)}--{slug(value)}"


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

LINK = {
    "SUPERSEDES": (
        "MATCH (f:Fact {name: $name}) MERGE (o:Fact {name: $other}) MERGE (f)-[:SUPERSEDES]->(o)"
    ),
    "DERIVED_FROM": (
        "MATCH (f:Fact {name: $name}) MERGE (o:Fact {name: $other}) MERGE (f)-[:DERIVED_FROM]->(o)"
    ),
}


def add_fact(
    vault: Path,
    *,
    entity: str,
    predicate: str,
    value: str,
    agent: str,
    source: str,
    statement: str = "",
    supersedes: list[str] | None = None,
    derived_from: list[str] | None = None,
) -> str:
    """Write one fact into the working-tree vault. Returns the fact's name."""
    name = fact_name(entity, predicate, value)
    vault.mkdir(parents=True, exist_ok=True)
    with open_vault(vault, in_place=True) as conn:
        rows(
            conn,
            ADD_FACT,
            {
                "entity": entity,
                "agent": agent,
                "source": slug(source),
                "uri": source,
                "name": name,
                "predicate": predicate,
                "value": value,
                "statement": statement or f"{entity} {predicate}: {value}",
            },
        )
        for other in supersedes or []:
            rows(conn, LINK["SUPERSEDES"], {"name": name, "other": other})
        for other in derived_from or []:
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

"""Tell a read-only Cypher query from one that writes."""

from __future__ import annotations

import re

WRITE_CLAUSE = re.compile(
    r"(?<![.\w])(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|LOAD\s+CSV)(?!\w)", re.I
)
QUOTED = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`|//[^\n]*|/\*.*?\*/", re.S)


def is_read_only(cypher: str) -> bool:
    return not WRITE_CLAUSE.search(QUOTED.sub(" ", cypher))

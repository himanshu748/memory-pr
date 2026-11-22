"""Tell a read-only Cypher query from one that writes."""

from __future__ import annotations

import re

WRITE_CLAUSE = re.compile(
    r"(?<![.$:\w])(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|INSERT|LOAD\s+CSV)(?!\w)",
    re.I,
)
QUOTED = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`|//[^\n]*|/\*.*?\*/", re.S)
# Relate ships procedures that write (db.createLabel, db.createProperty, ...), so a CALL
# must be a subquery or name one of these, which only read.
CALL = re.compile(r"(?<![.$:\w])CALL(?!\w)\s*([^\s({]*)", re.I)
READ_PROCEDURES = frozenset(
    {
        "db.labels",
        "db.relationshiptypes",
        "db.propertykeys",
        "db.schema.visualization",
        "db.schema.nodetypeproperties",
        "db.schema.reltypeproperties",
        "db.index.fulltext.querynodes",
        "db.index.fulltext.queryrelationships",
        "db.index.vector.querynodes",
        "db.index.vector.queryrelationships",
    }
)


def is_read_only(cypher: str) -> bool:
    # Text and comments go; a name in backticks leaves one backtick, so it is still a name.
    bare = QUOTED.sub(lambda quoted: "`" if quoted[0].startswith("`") else " ", cypher)
    if WRITE_CLAUSE.search(bare):
        return False
    # A procedure named with backticks is never on the list, so `db`.createLabel is refused.
    return all(name == "" or name.lower() in READ_PROCEDURES for name in CALL.findall(bare))

"""Read a Markdown vault as a graph with Relate, and lint the files Relate reads."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import relate
import yaml

GENERATED = {"index.md", "log.md"}
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)

# A triple is one graph fact:
#   ("node", id) | ("prop", id, key, json_value) | ("rel", src_id, TYPE, dst_id, json_props)
Triple = tuple[str, ...]


@dataclass(frozen=True)
class FileIssue:
    kind: str  # duplicate_key | bad_frontmatter | conflict_marker | name_mismatch
    path: str
    detail: str


@dataclass
class Graph:
    triples: frozenset[Triple]
    stubs: frozenset[str]  # node ids that are linked to but have no note
    issues: tuple[FileIssue, ...] = ()
    findings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class _DupKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _DupKeyLoader, node: yaml.MappingNode, deep: bool = False):
    seen: dict[Any, int] = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        line = key_node.start_mark.line + 2  # +1 for the opening '---', +1 for 1-based
        if key in seen:
            raise DuplicateKey(str(key), seen[key], line)
        seen[key] = line
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


class DuplicateKey(Exception):
    def __init__(self, key: str, first: int, second: int):
        super().__init__(key)
        self.key, self.first, self.second = key, first, second


_DupKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def note_files(vault: Path) -> list[Path]:
    return sorted(
        p for p in vault.rglob("*.md") if p.name not in GENERATED and ".relate" not in p.parts
    )


def node_id(vault: Path, path: Path) -> str:
    rel = path.relative_to(vault).with_suffix("")
    return rel.as_posix() if len(rel.parts) > 1 else f"Vault/{rel.name}"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[text.index("\n") + 1 : end + 1], text[end + 4 :]


def lint(vault: Path) -> tuple[list[FileIssue], set[str]]:
    """File-level problems Relate does not report, and the ids of the notes that exist."""
    issues: list[FileIssue] = []
    ids: set[str] = set()
    for path in note_files(vault):
        rel = path.relative_to(vault).as_posix()
        ids.add(node_id(vault, path))
        text = path.read_text(encoding="utf-8", errors="replace")
        if CONFLICT_MARKER.search(text):
            issues.append(FileIssue("conflict_marker", rel, "unresolved git conflict markers"))
            continue
        front, _ = split_frontmatter(text)
        if front is None:
            issues.append(FileIssue("bad_frontmatter", rel, "no YAML frontmatter block"))
            continue
        try:
            data = yaml.load(front, Loader=_DupKeyLoader)  # noqa: S506 - SafeLoader subclass
        except DuplicateKey as dup:
            issues.append(
                FileIssue(
                    "duplicate_key",
                    rel,
                    f"key `{dup.key}` is set twice (lines {dup.first} and {dup.second}); "
                    "Relate reads this note as empty",
                )
            )
            continue
        except yaml.YAMLError as err:
            issues.append(FileIssue("bad_frontmatter", rel, f"invalid YAML: {err}".split("\n")[0]))
            continue
        if not isinstance(data, dict):
            issues.append(FileIssue("bad_frontmatter", rel, "frontmatter is not a mapping"))
            continue
        name = data.get("name")
        if name is not None and str(name) != path.stem:
            issues.append(
                FileIssue("name_mismatch", rel, f"`name: {name}` does not match the file name")
            )
    return issues, ids


@contextmanager
def open_vault(vault: Path, *, in_place: bool = False) -> Iterator[relate.Connection]:
    """Open a vault with Relate's Markdown store.

    Reads work on a throwaway copy so that Relate's sidecar and regenerated index
    files never touch the checkout; `in_place=True` is for writes.
    """
    previous = os.environ.get("RELATE_SNAPSHOT_FORMAT")
    os.environ["RELATE_SNAPSHOT_FORMAT"] = "markdown"
    tmp: str | None = None
    try:
        target = vault
        if not in_place:
            tmp = tempfile.mkdtemp(prefix="mempr-")
            target = Path(tmp) / "vault"
            shutil.copytree(
                vault, target, ignore=shutil.ignore_patterns(".relate", ".cypherlite.*")
            )
        conn = relate.connect(str(target))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    finally:
        if previous is None:
            os.environ.pop("RELATE_SNAPSHOT_FORMAT", None)
        else:
            os.environ["RELATE_SNAPSHOT_FORMAT"] = previous
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def rows(conn: relate.Connection, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
    cur = conn.cursor()
    cur.execute(cypher, params or {})
    return [dict(r) for r in cur.fetchall()]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


NODES = "MATCH (n) RETURN labels(n)[0] AS label, n.name AS name, properties(n) AS props"
RELS = (
    "MATCH (a)-[r]->(b) RETURN labels(a)[0] AS al, a.name AS an, type(r) AS t, "
    "labels(b)[0] AS bl, b.name AS bn, properties(r) AS props"
)


def extract(
    conn: relate.Connection, note_ids: set[str]
) -> tuple[frozenset[Triple], frozenset[str]]:
    triples: set[Triple] = set()
    stubs: set[str] = set()
    for row in rows(conn, NODES):
        nid = f"{row['label']}/{row['name']}"
        if nid not in note_ids:
            stubs.add(nid)
            continue
        triples.add(("node", nid))
        for key, value in row["props"].items():
            if key != "name":
                triples.add(("prop", nid, key, _json(value)))
    for row in rows(conn, RELS):
        src = f"{row['al']}/{row['an']}"
        dst = f"{row['bl']}/{row['bn']}"
        triples.add(("rel", src, row["t"], dst, _json(row["props"])))
    return frozenset(triples), frozenset(stubs)


MARK_STUB = "MATCH (n) WHERE labels(n)[0] + '/' + n.name IN $stubs SET n.stub = true"


def load(vault: Path, rules: list[Any] | None = None) -> Graph:
    """Lint the files, load them with Relate, and run `rules` on the loaded graph."""
    issues, ids = lint(vault)
    if not ids:
        return Graph(frozenset(), frozenset(), tuple(issues))
    with open_vault(vault) as conn:
        triples, stubs = extract(conn, ids)
        findings: dict[str, list[dict[str, Any]]] = {}
        if rules:
            if stubs:
                rows(conn, MARK_STUB, {"stubs": sorted(stubs)})
            for rule in rules:
                findings[rule.id] = rows(conn, rule.cypher)
    return Graph(triples, stubs, tuple(issues), findings)

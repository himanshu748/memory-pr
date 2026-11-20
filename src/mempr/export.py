"""Export the memory graph and pull request reviews as JSON for the browser view."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from . import gitutil
from .check import Review, review
from .report import _count, render
from .rules import Rule, load_rules
from .vault import Graph, Triple, load, node_id, note_files

FORMAT = 1
NODE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*/[A-Za-z0-9._-]+")


def graph_dict(graph: Graph, notes: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Nodes and relationships as Relate loaded them, each note with its file and text."""
    nodes: dict[str, dict[str, Any]] = {}

    def node(nid: str) -> dict[str, Any]:
        label, name = nid.split("/", 1)
        return nodes.setdefault(nid, {"id": nid, "label": label, "props": {"name": name}})

    rels = []
    for t in sorted(graph.triples):
        if t[0] == "node":
            node(t[1]).update(notes.get(t[1], {}))
        elif t[0] == "prop":
            node(t[1])["props"][t[2]] = json.loads(t[3])
        else:
            rels.append({"source": t[1], "type": t[2], "target": t[3], "props": json.loads(t[4])})
    for nid in graph.stubs:  # linked to, but there is no note
        node(nid)["stub"] = True
    for issue in graph.issues:
        nid = _path_id(issue.path)
        if nid in nodes:
            nodes[nid].setdefault("issues", []).append(issue.detail)
    return {"nodes": [nodes[k] for k in sorted(nodes)], "rels": rels}


def _path_id(path: str) -> str:
    """`Fact/x.md` (relative to the vault) -> `Fact/x`."""
    return node_id(Path("."), Path(path))


def snapshot(repo: Path, treeish: str, vault: str, root: Path) -> dict[str, Any]:
    """The graph at `treeish`, read by Relate from an export of the tree under `root`."""
    path = gitutil.export_tree(repo, treeish, vault, root)
    notes = {
        node_id(path, f): {
            "file": f"{vault}/{f.relative_to(path).as_posix()}",
            "text": f.read_text(encoding="utf-8", errors="replace"),
        }
        for f in note_files(path)
    }
    return graph_dict(load(path), notes)


def _ids_in(triples: list[Triple]) -> set[str]:
    ids: set[str] = set()
    for t in triples:
        ids.add(t[1])
        if t[0] == "rel":
            ids.add(t[3])
    return ids


def _ids_in_row(row: dict[str, Any], by_name: dict[str, set[str]], known: set[str]) -> set[str]:
    """Node ids a finding's row names, as a bare note name or as `Label/name` in a path."""
    ids: set[str] = set()
    for value in row.values():
        if not isinstance(value, str):
            continue
        ids |= by_name.get(value, set())
        ids |= {m for m in NODE_ID.findall(value) if m in known}
    return ids


def problems(result: Review, after: dict[str, Any]) -> list[dict[str, Any]]:
    """Each problem of the review with the nodes and relationships it is about."""
    known = {n["id"] for n in after["nodes"]}
    by_name: dict[str, set[str]] = {}
    for n in after["nodes"]:
        by_name.setdefault(n["props"]["name"], set()).add(n["id"])
    rels = [(r["source"], r["type"], r["target"]) for r in after["rels"]]
    out: list[dict[str, Any]] = []

    def add(kind: str, title: str, text: str, nodes: set[str], extra_rels=(), **more) -> None:
        # What replaced a fact is part of why it is a problem, so draw it too.
        nodes = nodes | {s for s, t, d in rels if t == "SUPERSEDES" and d in nodes}
        drawn = {r for r in rels if r[0] in nodes and r[2] in nodes} | set(extra_rels)
        out.append(
            {
                "kind": kind,
                "severity": "block",
                "title": title,
                "text": text,
                "nodes": sorted(nodes),
                "rels": [list(r) for r in sorted(drawn)],
                **more,
            }
        )

    if result.git_conflicts:
        add(
            "git_conflict",
            "Git cannot merge these notes",
            ", ".join(result.git_conflicts),
            {_path_id(p.split("/", 1)[-1]) for p in result.git_conflicts},
        )
    for c in result.property_conflicts:
        add(
            "property_conflict",
            "Both branches changed the same property",
            f"{c.node}.{c.key} is {json.loads(c.base_value)!r} on the base branch and "
            f"{json.loads(c.head_value)!r} in this pull request.",
            {c.node},
        )
    if result.dropped:
        add(
            "dropped",
            f"This merge would silently drop {_count(result.dropped)}",
            "Git reports a clean merge, but these facts are gone after it although neither "
            "branch removed them.",
            _ids_in(result.dropped),
            [(t[1], t[2], t[3]) for t in result.dropped if t[0] == "rel"],
        )
    for issue in result.file_issues:
        add("file_issue", f"Broken note {issue.path}", issue.detail, {_path_id(issue.path)})
    for f in result.findings:
        add(
            "finding",
            f.rule.title,
            f.text,
            _ids_in_row(f.row, by_name, known),
            rule=f.rule.id,
            fix=f.rule.fix,
        )
        out[-1]["severity"] = f.rule.severity
    return out


def _commit(repo: Path, rev: str) -> dict[str, str]:
    subject, author, body = (
        gitutil.git(repo, "log", "-1", "--format=%s%x00%an%x00%b", rev).stdout[:-1].split("\x00")
    )
    return {"title": subject, "author": author, "description": body.strip()}


def export_pull(
    repo: Path, base: str, head: str, vault: str, rules: list[Rule], root: Path
) -> dict[str, Any]:
    """One pull request: the review, and the graph before and after the merge."""
    result = review(repo, base, head, vault, rules)
    merge = gitutil.merge_tree(repo, result.base, result.head)
    # When git refuses the merge, the check reviews the branch on its own; draw that.
    after = snapshot(repo, merge.tree or result.head, vault, root / "after")
    return {
        "id": head,
        **_commit(repo, result.head),
        "blocked": result.blocked,
        "problems": problems(result, after),
        "review": result.to_dict(),
        "comment": render(result),
        "before": snapshot(repo, result.base, vault, root / "before"),
        "after": after,
    }


def export(repo: Path, rev: str, pulls: list[str], vault: str, rules_file: str) -> dict[str, Any]:
    """The memory at `rev`, the review rules, and each pull (`HEAD` or `BASE..HEAD`)."""
    sha = gitutil.rev_parse(repo, rev)
    out: dict[str, Any] = {"format": FORMAT, "vault": vault, "rev": sha}
    with tempfile.TemporaryDirectory(prefix="mempr-export-") as tmp:
        out["memory"] = snapshot(repo, sha, vault, Path(tmp) / "memory")
        out["pulls"] = []
        for i, spec in enumerate(pulls):
            base, _, head = spec.rpartition("..")
            base = base or rev
            # Rules come from the base branch, exactly as in `mempr check`.
            rules = load_rules(gitutil.read_file(repo, base, rules_file))
            pull = export_pull(repo, base, head, vault, rules, Path(tmp) / f"pull-{i}")
            if pull["review"]["base"] == sha:
                del pull["before"]  # same graph as `memory`
            else:
                pull["rules"] = [vars(rule) for rule in rules]  # may differ from `rev`'s
            out["pulls"].append(pull)
    rules = load_rules(gitutil.read_file(repo, rev, rules_file))
    out["rules"] = [vars(rule) for rule in rules]
    return out


def cmd_export(args: argparse.Namespace) -> int:
    data = export(Path(args.repo).resolve(), args.rev, args.pull, args.vault, args.rules)
    text = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    Path(args.out).write_text(text, "utf-8")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("export", help="write the graph and reviews as JSON for the browser")
    parser.add_argument("--rev", default="HEAD", help="revision whose memory is exported")
    parser.add_argument(
        "--pull",
        action="append",
        default=[],
        metavar="[BASE..]HEAD",
        help="also review merging HEAD into BASE (default: --rev); repeatable",
    )
    parser.add_argument(
        "--rules", default="memory-rules.yaml", help="extra rules file, read from the base branch"
    )
    parser.add_argument("--out", required=True, help="JSON file to write")
    parser.set_defaults(func=cmd_export)

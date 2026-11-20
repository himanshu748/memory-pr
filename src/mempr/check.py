"""Review a memory change as a graph: what it adds, what the merge loses, what it breaks."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitutil
from .rules import Rule
from .vault import FileIssue, Graph, Triple, load


@dataclass(frozen=True)
class PropertyConflict:
    node: str
    key: str
    base_value: str
    head_value: str


@dataclass(frozen=True)
class Finding:
    rule: Rule
    row: dict[str, Any]

    @property
    def text(self) -> str:
        return self.rule.message.format(**self.row)


@dataclass
class Review:
    base: str
    head: str
    merge_base: str
    added: list[Triple] = field(default_factory=list)
    removed: list[Triple] = field(default_factory=list)
    git_conflicts: list[str] = field(default_factory=list)
    property_conflicts: list[PropertyConflict] = field(default_factory=list)
    dropped: list[Triple] = field(default_factory=list)
    file_issues: list[FileIssue] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.rule.severity == "block"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.rule.severity == "warn"]

    @property
    def problem_count(self) -> int:
        return (
            len(self.git_conflicts)
            + len(self.property_conflicts)
            + len(self.file_issues)
            + (1 if self.dropped else 0)
            + len(self.blocking)
        )

    @property
    def blocked(self) -> bool:
        return self.problem_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "head": self.head,
            "merge_base": self.merge_base,
            "blocked": self.blocked,
            "problems": self.problem_count,
            "added": [list(t) for t in self.added],
            "removed": [list(t) for t in self.removed],
            "git_conflicts": self.git_conflicts,
            "property_conflicts": [vars(c) for c in self.property_conflicts],
            "dropped": [list(t) for t in self.dropped],
            "file_issues": [vars(i) for i in self.file_issues],
            "findings": [
                {"rule": f.rule.id, "severity": f.rule.severity, "text": f.text, "row": f.row}
                for f in self.findings
            ],
        }


def _row_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _new_findings(rules: list[Rule], before: Graph, after: Graph) -> list[Finding]:
    found = []
    for rule in rules:
        known = {_row_key(r) for r in before.findings.get(rule.id, [])}
        for row in after.findings.get(rule.id, []):
            if _row_key(row) not in known:
                found.append(Finding(rule, row))
    return found


def _new_issues(before: Graph, after: Graph) -> list[FileIssue]:
    known = set(before.issues)
    return [issue for issue in after.issues if issue not in known]


def _property_conflicts(
    ancestor: frozenset[Triple], target: frozenset[Triple], head: frozenset[Triple]
) -> list[PropertyConflict]:
    """Both sides gave the same property of the same node a different new value."""

    def changed(side: frozenset[Triple]) -> dict[tuple[str, str], str]:
        return {(t[1], t[2]): t[3] for t in side - ancestor if t[0] == "prop"}

    ours, theirs = changed(target), changed(head)
    return [
        PropertyConflict(node, key, ours[(node, key)], theirs[(node, key)])
        for (node, key) in sorted(ours.keys() & theirs.keys())
        if ours[(node, key)] != theirs[(node, key)]
    ]


def three_way(
    ancestor: frozenset[Triple],
    target: frozenset[Triple],
    head: frozenset[Triple],
    merged: frozenset[Triple],
) -> tuple[list[PropertyConflict], list[Triple]]:
    """Compare git's merge with the merge of the two graphs.

    The graph merge keeps everything on the target, applies what the head added and
    removed since the common ancestor, and must not lose a fact neither side removed.
    """
    conflicts = _property_conflicts(ancestor, target, head)
    contested = {(c.node, c.key) for c in conflicts}
    expected = (target - (ancestor - head)) | (head - ancestor)
    dropped = sorted(
        t for t in expected - merged if not (t[0] == "prop" and (t[1], t[2]) in contested)
    )
    return conflicts, dropped


def review(repo: Path, base: str, head: str, vault: str, rules: list[Rule]) -> Review:
    """Review merging `head` into `base` for the vault at `vault` inside `repo`."""
    base_sha = gitutil.rev_parse(repo, base)
    head_sha = gitutil.rev_parse(repo, head)
    ancestor_sha = gitutil.merge_base(repo, base_sha, head_sha)
    result = Review(base=base_sha, head=head_sha, merge_base=ancestor_sha)

    with tempfile.TemporaryDirectory(prefix="mempr-review-") as tmp:
        root = Path(tmp)

        def graph_at(treeish: str, name: str, with_rules: bool) -> Graph:
            path = gitutil.export_tree(repo, treeish, vault, root / name)
            return load(path, rules if with_rules else None)

        ancestor = graph_at(ancestor_sha, "ancestor", False)
        target = graph_at(base_sha, "base", True)
        tip = graph_at(head_sha, "head", True)
        result.added = sorted(tip.triples - ancestor.triples)
        result.removed = sorted(ancestor.triples - tip.triples)

        merge = gitutil.merge_tree(repo, base_sha, head_sha)
        if merge.tree is None:
            # Git already refuses this merge; still review the branch on its own.
            result.git_conflicts = [
                c for c in merge.conflicts if c.startswith(vault + "/")
            ] or list(merge.conflicts)
            result.file_issues = _new_issues(target, tip)
            result.findings = _new_findings(rules, target, tip)
            return result

        merged = graph_at(merge.tree, "merged", True)
        result.property_conflicts, result.dropped = three_way(
            ancestor.triples, target.triples, tip.triples, merged.triples
        )
        result.file_issues = _new_issues(target, merged)
        result.findings = _new_findings(rules, target, merged)
    return result

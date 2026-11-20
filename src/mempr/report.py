"""Render a review as the pull request comment."""

from __future__ import annotations

import json
from collections import Counter

from .check import Review
from .vault import Triple

MARKER = "<!-- mempr-review -->"
LIST_LIMIT = 25


def fact(t: Triple) -> str:
    if t[0] == "node":
        return f"note `{t[1]}`"
    if t[0] == "prop":
        value = json.loads(t[3])
        text = value if isinstance(value, str) else json.dumps(value)
        if len(text) > 80:
            text = text[:77] + "..."
        return f"`{t[1]}`.{t[2]} = `{text}`"
    props = json.loads(t[4])
    suffix = f" {json.dumps(props)}" if props else ""
    return f"`{t[1]}` -[:{t[2]}]-> `{t[3]}`{suffix}"


def _count(triples: list[Triple]) -> str:
    kinds = Counter(t[0] for t in triples)
    names = {"node": "note", "rel": "link", "prop": "property"}
    plural = {"node": "notes", "rel": "links", "prop": "properties"}
    parts = [
        f"{kinds[k]} {names[k] if kinds[k] == 1 else plural[k]}"
        for k in ("node", "rel", "prop")
        if kinds[k]
    ]
    return ", ".join(parts) or "nothing"


def _bullets(triples: list[Triple], sign: str) -> list[str]:
    order = {"node": 0, "rel": 1, "prop": 2}
    ordered = sorted(triples, key=lambda t: (order[t[0]], t))
    lines = [f"- {sign} {fact(t)}" for t in ordered[:LIST_LIMIT]]
    if len(ordered) > LIST_LIMIT:
        lines.append(f"- ... and {len(ordered) - LIST_LIMIT} more")
    return lines


def render(review: Review) -> str:
    out = [MARKER]
    if review.blocked:
        n = review.problem_count
        out.append(f"## Memory review: blocked ({n} problem{'s' if n != 1 else ''})")
    else:
        out.append("## Memory review: passed")
    out.append("")
    out.append(
        f"This pull request adds **{_count(review.added)}** and removes "
        f"**{_count(review.removed)}** in the memory graph."
    )
    out.append("")

    number = 0

    def problem(title: str) -> None:
        nonlocal number
        number += 1
        out.append(f"### {number}. {title}")

    if review.git_conflicts:
        problem("Git cannot merge these notes")
        out += [f"- `{path}`" for path in review.git_conflicts]
        out += ["", "Rebase the branch on the base branch and resolve the conflicts.", ""]

    for conflict in review.property_conflicts:
        problem("Both branches changed the same property")
        out.append(
            f"`{conflict.node}`.{conflict.key} is `{json.loads(conflict.base_value)}` on the "
            f"base branch and `{json.loads(conflict.head_value)}` in this pull request. "
            "Git merges this without a conflict, but the graph can hold only one value."
        )
        out += ["", "Rebase and keep one value.", ""]

    if review.dropped:
        problem(f"This merge would silently drop {_count(review.dropped)}")
        out.append(
            "Git reports a clean merge, but these facts exist before the merge and are "
            "gone after it, although neither branch removed them:"
        )
        out += _bullets(review.dropped, "lost")
        out.append("")

    for issue in review.file_issues:
        problem(f"Broken note `{issue.path}`")
        out += [issue.detail[0].upper() + issue.detail[1:] + ".", ""]

    for finding in review.blocking:
        problem(finding.rule.title)
        out.append(finding.text)
        if finding.rule.fix:
            out += ["", f"Fix: {finding.rule.fix}"]
        out.append("")

    if review.warnings:
        out.append("### Warnings")
        out += [f"- {f.rule.title}: {f.text}" for f in review.warnings]
        out.append("")

    if review.added or review.removed:
        out.append("<details><summary>Graph changes in this pull request</summary>")
        out.append("")
        out += _bullets(review.added, "added")
        out += _bullets(review.removed, "removed")
        out += ["", "</details>", ""]

    out.append(
        f"<sub>Checked by mempr with Relate: base `{review.base[:7]}`, head "
        f"`{review.head[:7]}`, common ancestor `{review.merge_base[:7]}`. Each rule is a "
        "Cypher query over the merged graph.</sub>"
    )
    return "\n".join(out) + "\n"

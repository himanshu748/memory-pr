"""Replay the shipped restore-drill example with one dependency edge removed."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mempr.check import review_vaults
from mempr.rules import default_rules
from mempr.search import search
from mempr.vault import load

VAULT = Path(__file__).resolve().parents[2] / "memory"
BACKUP = "payments-api--backup-job--nightly-pg-dump"
RESTORE = "payments-api--restore-drill--quarterly-pg-restore"
EDGE_TEXT = f'DERIVED_FROM:\n  - "[[Fact/{BACKUP}]]"\n'


def replace_once(note: Path, before: str, after: str) -> None:
    text = note.read_text(encoding="utf-8")
    if text.count(before) != 1:
        raise ValueError(f"Expected exactly one matching line in {note.name}")
    note.write_text(text.replace(before, after, 1), encoding="utf-8")


def observe(before: Path, after: Path) -> dict[str, Any]:
    recall = search(before, "postgres")
    review = review_vaults(before, after, default_rules())
    return {
        "keyword_hits": [
            {"fact": hit["fact"], "value": hit["value"]} for hit in recall["hits"]
        ],
        "restore_drill_hops": next(
            (row["hops"] for row in recall["connected"] if row["fact"] == RESTORE),
            None,
        ),
        "proposal_blocked": review.blocked,
        "blockers": [
            {
                "rule": finding.rule.id,
                "fact": finding.row.get("fact"),
                "path": finding.row.get("path"),
            }
            for finding in review.blocking
        ],
        "added": review.added,
        "removed": review.removed,
    }


def main() -> None:
    with TemporaryDirectory(prefix="memory-pr-relationship-") as folder:
        root = Path(folder)
        linked = shutil.copytree(VAULT, root / "linked")
        detached = shutil.copytree(linked, root / "detached")
        replace_once(detached / "Fact" / f"{RESTORE}.md", EDGE_TEXT, "")

        linked_graph, detached_graph = load(linked), load(detached)
        (removed_edge,) = linked_graph.triples - detached_graph.triples
        assert removed_edge[:4] == (
            "rel", f"Fact/{RESTORE}", "DERIVED_FROM", f"Fact/{BACKUP}"
        )
        assert not detached_graph.triples - linked_graph.triples

        observations = {}
        for before in (linked, detached):
            after = shutil.copytree(before, root / f"{before.name}-proposal")
            replace_once(
                after / "Fact" / f"{BACKUP}.md", "status: active\n", "status: retracted\n"
            )
            observations[before.name] = observe(before, after)

    intact, cut = observations["linked"], observations["detached"]
    assert intact["keyword_hits"] == cut["keyword_hits"]
    assert any(hit["value"] == "postgres-15" for hit in intact["keyword_hits"])
    assert intact["added"] == cut["added"]
    assert intact["removed"] == cut["removed"]
    assert intact["restore_drill_hops"] == 2
    assert cut["restore_drill_hops"] is None
    assert intact["proposal_blocked"] and not cut["proposal_blocked"]
    assert {(row["rule"], row["fact"]) for row in intact["blockers"]} == {
        ("stale-derivation", RESTORE)
    }
    print(json.dumps({"removed_relationship": removed_edge, **observations}, indent=2))


if __name__ == "__main__":
    main()

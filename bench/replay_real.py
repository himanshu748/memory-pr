"""Replay the committed real-repository benchmark without contacting GitHub."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

import yaml

from mempr.check import review_vaults
from mempr.rules import default_rules
from mempr.vault import note_files, split_frontmatter

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "bench" / "real-memory"
REPORT = ROOT / "docs" / "real-repos.json"


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    notes = {}
    for path in note_files(VAULT):
        text = path.read_text(encoding="utf-8")
        front, body = split_frontmatter(text)
        key = path.relative_to(VAULT).with_suffix("").as_posix()
        notes[key] = (text, yaml.safe_load(front or "{}"), body)

    def metadata(link: str) -> dict:
        return notes[link.removeprefix("[[").removesuffix("]]")][1]

    def source(fact: dict) -> str:
        return metadata(fact["SOURCE"][0])["uri"]

    expected_changes = Counter(
        (row["predicate"], row["old"], row["new"], row["old_url"], row["new_url"])
        for repo in report["results"]
        for row in repo["changed"]
    )
    changes = Counter()
    earlier_facts = 0
    rules = default_rules()
    with tempfile.TemporaryDirectory(prefix="mempr-real-replay-") as directory:
        before, stale = Path(directory, "before"), Path(directory, "stale")
        for key, (text, fields, body) in notes.items():
            old_fact = "[[Agent/scout]]" in fields.get("ASSERTED_BY", [])
            if fields.get("type") != "Fact" or old_fact:
                path = before / f"{key}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                earlier_facts += int(old_fact)

            replaced = fields.get("SUPERSEDES", [])
            if replaced:
                for link in replaced:
                    old = metadata(link)
                    changes[
                        fields["predicate"],
                        old["value"],
                        fields["value"],
                        source(old),
                        source(fields),
                    ] += 1
                without_supersedes = {k: v for k, v in fields.items() if k != "SUPERSEDES"}
                front = yaml.safe_dump(without_supersedes, sort_keys=False, allow_unicode=True)
                text = f"---\n{front}---{body}"
            path = stale / f"{key}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        stale_review = review_vaults(before, stale, rules)
        fixed_review = review_vaults(before, VAULT, rules)

    stale_findings = Counter(finding.rule.id for finding in stale_review.blocking)
    if (
        len(report["results"]) != report["read"]
        or earlier_facts != report["facts_learned_then"]
        or changes != expected_changes
        or changes.total() != report["facts_changed"]
        or stale_findings != {"contradiction": report["blocking_findings_stale"]}
        or fixed_review.blocked != report["blocked_fixed"]
    ):
        raise SystemExit("The archived vault and benchmark report disagree.")

    print(f"Repositories in the report: {report['read']}")
    print(f"Earlier facts: {earlier_facts}")
    print(f"Changed facts with matching source pairs: {changes.total()}")
    print(f"Blocking findings before supersession: {len(stale_review.blocking)}")
    print(f"Blocking findings after supersession: {len(fixed_review.blocking)}")


if __name__ == "__main__":
    main()

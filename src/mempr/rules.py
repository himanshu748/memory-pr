"""Review rules are Cypher queries: each returned row is one finding."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str  # block | warn
    title: str
    message: str  # str.format template over the row's columns
    cypher: str
    fix: str = ""


def _parse(text: str) -> list[Rule]:
    rules = []
    for item in yaml.safe_load(text) or []:
        if item.get("severity") not in ("block", "warn"):
            raise ValueError(f"rule {item.get('id')!r}: severity must be 'block' or 'warn'")
        rules.append(
            Rule(
                id=item["id"],
                severity=item["severity"],
                title=item["title"],
                message=item["message"],
                cypher=item["cypher"],
                fix=item.get("fix", ""),
            )
        )
    return rules


def default_rules() -> list[Rule]:
    return _parse(resources.files("mempr").joinpath("default_rules.yaml").read_text("utf-8"))


def load_rules(path: Path | None) -> list[Rule]:
    """Default rules, with rules from `path` added or replacing a default with the same id."""
    merged = {rule.id: rule for rule in default_rules()}
    if path and path.exists():
        for rule in _parse(path.read_text("utf-8")):
            merged[rule.id] = rule
    return list(merged.values())

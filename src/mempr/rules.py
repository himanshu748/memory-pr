"""Review rules are Cypher queries: each returned row is one finding."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

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


def load_rules(extra: str | None) -> list[Rule]:
    """Default rules, with rules from the YAML text `extra` added or replacing a default."""
    merged = {rule.id: rule for rule in default_rules()}
    for rule in _parse(extra) if extra else []:
        merged[rule.id] = rule
    return list(merged.values())

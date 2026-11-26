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


class RuleConfigError(ValueError):
    """The rule configuration cannot be applied."""


def _parse(text: str) -> list[Rule]:
    try:
        items = yaml.safe_load(text)
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise RuleConfigError(f"invalid YAML{where}") from err
    if items is None:
        return []
    if not isinstance(items, list):
        raise RuleConfigError("expected a list of review rules")

    rules = []
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise RuleConfigError(f"rule #{number}: expected a mapping")
        for field in ("id", "severity", "title", "message", "cypher"):
            if not isinstance(item.get(field), str):
                raise RuleConfigError(f"rule #{number}: {field!r} must be a string")
        if not isinstance(item.get("fix", ""), str):
            raise RuleConfigError(f"rule #{number}: 'fix' must be a string")
        if item["severity"] not in ("block", "warn"):
            raise RuleConfigError(f"rule {item['id']!r}: severity must be 'block' or 'warn'")
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

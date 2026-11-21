"""Review a draft from the page's "Write a memory" form on the server, with no git.

A draft is one change: a fact to add (optionally replacing others and retracting others)
or a fact to retract. The page reviews it in the browser with Relate's WebAssembly build;
this module reviews the same draft with `mempr check`'s rules on the committed memory, so
the two can be compared. Nothing here writes outside a temporary folder.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from . import memory
from .check import review_vaults
from .report import MARKER, render
from .rules import Rule
from .vault import note_files

MAX_MERGED = 12  # drafts already merged into the page's memory
MAX_LINKS = 12  # facts one draft may replace or retract
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
FACT_NAME = re.compile(r"[a-z0-9]+(?:-+[a-z0-9]+)*")  # entity--predicate--value


class BadDraft(ValueError):
    """The request is not a draft this server accepts."""


def _slug(draft: dict, key: str, limit: int = 80) -> str:
    text = draft.get(key)
    if not isinstance(text, str) or len(text) > limit or not SLUG.fullmatch(text):
        raise BadDraft(f"{key} must be lowercase letters, digits and hyphens, {limit} at most")
    return text


def _text(draft: dict, key: str, limit: int, required: bool) -> str:
    text = draft.get(key, "")
    if not isinstance(text, str) or len(text) > limit or "\n" in text or "\r" in text:
        raise BadDraft(f"{key} must be one line of text, {limit} characters at most")
    text = text.strip()
    if required and not text:
        raise BadDraft(f"{key} is required")
    return text


def _fact(name: Any, key: str) -> str:
    if not isinstance(name, str) or len(name) > 200 or not FACT_NAME.fullmatch(name):
        raise BadDraft(f"{key} must name facts: lowercase letters, digits and hyphens")
    return name


def _facts(draft: dict, key: str) -> list[str]:
    names = draft.get(key, [])
    if not isinstance(names, list) or len(names) > MAX_LINKS:
        raise BadDraft(f"{key} must be a list of at most {MAX_LINKS} fact names")
    return [_fact(name, key) for name in names]


def clean(draft: Any) -> dict[str, Any]:
    """The draft with only the fields the review uses, each checked. Raises BadDraft."""
    if not isinstance(draft, dict):
        raise BadDraft("a draft must be an object")
    if draft.get("kind", "fact") == "retract":
        return {
            "kind": "retract",
            "target": _fact(draft.get("target"), "target"),
            "retract": _facts(draft, "retract"),
        }
    if draft.get("kind", "fact") != "fact":
        raise BadDraft("kind must be fact or retract")
    return {
        "kind": "fact",
        "agent": _slug(draft, "agent"),
        "entity": _slug(draft, "entity"),
        "predicate": _slug(draft, "predicate"),
        "value": _text(draft, "value", 120, required=True),
        "source": _text(draft, "source", 200, required=False),
        "supersedes": _facts(draft, "supersedes"),
        "retract": _facts(draft, "retract"),
    }


def apply(vault: Path, draft: dict[str, Any]) -> None:
    """Make the change a cleaned draft describes, with the same calls the command line uses."""
    if draft["kind"] == "retract":
        for fact in [draft["target"], *draft["retract"]]:
            memory.retract(vault, fact)
        return
    memory.add_fact(
        vault,
        entity=draft["entity"],
        predicate=draft["predicate"],
        value=draft["value"],
        agent=draft["agent"],
        source=draft["source"] or None,
        supersedes=draft["supersedes"],
    )
    for fact in draft["retract"]:
        memory.retract(vault, fact)


def _copy(source: Path, target: Path) -> Path:
    for note in note_files(source):
        to = target / note.relative_to(source)
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(note, to)
    return target


def review_request(body: Any, vault: Path, rules: list[Rule]) -> dict[str, Any]:
    """Review `body["draft"]` against `vault` with `body["merged"]` applied first.

    Raises BadDraft for a request that is not well formed or names a fact that is not there.
    """
    if not isinstance(body, dict):
        raise BadDraft("the body must be a JSON object with a draft")
    merged = body.get("merged", [])
    if not isinstance(merged, list) or len(merged) > MAX_MERGED:
        raise BadDraft(f"merged must be a list of at most {MAX_MERGED} drafts")
    merged = [clean(d) for d in merged]
    draft = clean(body.get("draft"))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="mempr-playground-") as tmp:
        before = _copy(vault, Path(tmp) / "before")
        try:
            for earlier in merged:
                apply(before, earlier)
            after = _copy(before, Path(tmp) / "after")
            apply(after, draft)
        except (KeyError, ValueError) as error:
            raise BadDraft(
                str(error.args[0]) if error.args else "the draft cannot be applied"
            ) from error
        result = review_vaults(before, after, rules, base="memory", head="draft")
    return {
        "blocked": result.blocked,
        "problems": result.problem_count,
        "rules": sorted({finding.rule.id for finding in result.blocking}),
        "review": result.to_dict(),
        "comment": render(result).replace(MARKER + "\n", "", 1),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }

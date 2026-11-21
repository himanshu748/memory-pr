from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"api_{name}", ROOT / "api" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_opens_the_committed_memory_and_counts_its_facts():
    body = load("health").health()
    notes = list((ROOT / "memory" / "Fact").glob("*.md"))
    assert body["ok"] is True
    assert body["facts"] == len([n for n in notes if n.name != "index.md"])
    assert body["relate"].startswith("1.")
    assert not (ROOT / "memory" / ".relate").exists()  # the checkout is left alone

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mempr import memory


class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.vault = path / "memory"

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True
        )
        return proc.stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def branch(self, name: str, start: str = "main") -> None:
        self.git("checkout", "-q", "-B", name, start)

    def add(self, entity: str, predicate: str, value: str, **kw) -> str:
        kw.setdefault("agent", "scout")
        kw.setdefault("source", f"docs/{entity}.md")
        return memory.add_fact(self.vault, entity=entity, predicate=predicate, value=value, **kw)

    def edit(self, note: str, old: str, new: str) -> None:
        path = self.vault / f"{note}.md"
        text = path.read_text()
        assert old in text
        path.write_text(text.replace(old, new, 1))


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path / "repo")
    r.path.mkdir()
    r.git("init", "-q", "-b", "main")
    r.git("config", "user.email", "test@example.com")
    r.git("config", "user.name", "Test")
    (r.path / ".gitignore").write_text(
        ".relate/\n.cypherlite.*\nmemory/**/index.md\nmemory/index.md\nmemory/log.md\n"
    )
    r.add("payments-api", "database", "postgres-15", statement="Stores data in Postgres 15.")
    r.add(
        "payments-api",
        "migration-tool",
        "pg-migrate",
        derived_from=["payments-api--database--postgres-15"],
    )
    r.add("checkout-web", "runtime", "node-22")
    r.commit("seed memory")
    return r

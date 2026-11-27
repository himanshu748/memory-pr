"""Probe the exact agent-proposed fixture from an isolated proof pull request."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "demo" / "ab" / "fixture"
COMPOSE = ["docker", "compose", "--project-name", "memory-pr-agent-proof"]


def test_agent_fixture_starts_and_pings_its_database():
    env = (FIXTURE / ".env").read_text(encoding="utf-8")
    if not any(
        line.startswith("DATABASE_URL=") and line.partition("=")[2]
        for line in env.splitlines()
    ):
        pytest.skip("The proof base precedes the agent's database proposal.")
    assert shutil.which("docker"), "The proof needs a real Docker Compose runtime."
    try:
        subprocess.run(
            [*COMPOSE, "up", "--build", "--wait", "--wait-timeout", "90"],
            cwd=FIXTURE,
            check=True,
            timeout=240,
        )
        for _ in range(10):
            try:
                with urlopen("http://127.0.0.1:8080/db", timeout=5) as response:
                    assert response.status == 200
                    assert json.load(response) == {"ok": True}
                return
            except URLError as error:
                last_error = error
                time.sleep(1)
        raise AssertionError(
            f"The unchanged service did not ping MongoDB: {last_error}"
        )
    finally:
        subprocess.run([*COMPOSE, "down", "-v"], cwd=FIXTURE, check=False, timeout=60)

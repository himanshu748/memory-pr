"""The page's server-check logic, run in node (skipped where node is not installed)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_server_check_module():
    """The page modules are ESM because package.json says so, with no npm install.

    Without that, Node treats site/*.js as CommonJS and the 13 tests fail to load.
    """
    meta = json.loads((ROOT / "package.json").read_text())
    assert meta["type"] == "module"
    assert not meta.get("dependencies")
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    env = dict(os.environ)
    probe = subprocess.run(
        ["node", "--no-experimental-detect-module", "-e", "0"],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        env["NODE_OPTIONS"] = "--no-experimental-detect-module"
    done = subprocess.run(
        ["node", "--test", *tests], cwd=ROOT, capture_output=True, text=True, env=env
    )
    assert done.returncode == 0, done.stdout + done.stderr

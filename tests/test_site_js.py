"""The page's server-check logic, run in node (skipped where node is not installed)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_server_check_module():
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    done = subprocess.run(["node", "--test", *tests], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr

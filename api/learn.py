"""GET /api/learn?repo=owner/name&months=12 (or baseline=previous): review real changes.

Reads only public files from GitHub, writes nothing to GitHub, and keeps nothing: the two
memories are written to a temporary folder for the one response.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mempr.realrepo import RepoError, learn  # noqa: E402
from mempr.rules import load_rules  # noqa: E402
from mempr.web import Endpoint, HttpError  # noqa: E402


class handler(Endpoint):  # noqa: N801 - the name Vercel's Python runtime looks for
    def get(self, query):
        baseline = query.get("baseline")
        if baseline is not None and baseline != "previous":
            raise HttpError(422, "baseline must be previous")
        try:
            months = None if baseline == "previous" else int(query.get("months", "12"))
        except ValueError as error:
            raise HttpError(422, "months must be a whole number") from error
        token = os.environ.get("GITHUB_READ_TOKEN") or os.environ.get("GITHUB_DISPATCH_TOKEN")
        try:
            return learn(query.get("repo", ""), months, load_rules(None), token=token)
        except RepoError as error:
            raise HttpError(error.status, str(error)) from error

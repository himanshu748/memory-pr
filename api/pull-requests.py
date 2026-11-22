"""POST /api/pull-requests asks GitHub to open a page draft as a real pull request.
GET /api/pull-requests/<id> says where that pull request stands.
GET /api/pull-requests says whether this deployment can open them.

The server never writes to the repository: a GitHub workflow does, after its own checks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mempr import playground_pr  # noqa: E402
from mempr.playground import BadDraft  # noqa: E402
from mempr.rules import load_rules  # noqa: E402
from mempr.web import Endpoint, HttpError  # noqa: E402


def rules():
    extra = ROOT / "memory-rules.yaml"
    return load_rules(extra.read_text() if extra.exists() else None)


def ident(path: str, query: dict) -> str | None:
    """The id from /api/pull-requests/<id>, or from ?id= when the path was rewritten."""
    tail = urlsplit(path).path.rstrip("/").rsplit("/", 1)[-1]
    return query.get("id") or (None if tail.startswith("pull-requests") else tail)


class handler(Endpoint):  # noqa: N801 (the name Vercel looks for)
    def get(self, query):
        wanted = ident(self.path, query)
        try:
            if wanted is None:
                return playground_pr.enabled(os.environ)
            return playground_pr.state(wanted, os.environ)
        except playground_pr.Refused as error:
            raise HttpError(error.status, error.message) from error

    def post(self, body):
        try:
            return playground_pr.open_request(body, ROOT / "memory", rules(), os.environ)
        except BadDraft as error:
            raise HttpError(422, str(error)) from error
        except playground_pr.Refused as error:
            raise HttpError(error.status, error.message) from error

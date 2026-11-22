"""POST /api/query: run a read-only Cypher query on the committed memory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mempr.agentapi import BadRequest, run_query  # noqa: E402
from mempr.web import Endpoint, HttpError  # noqa: E402


class handler(Endpoint):  # noqa: N801 (the name Vercel looks for)
    def post(self, body):
        try:
            return run_query(ROOT / "memory", body)
        except BadRequest as error:
            raise HttpError(error.status, str(error)) from error

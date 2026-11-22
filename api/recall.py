"""GET /api/recall?entity=payments-api: the current facts about one entity, with their sources."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mempr.agentapi import BadRequest, recall_entity  # noqa: E402
from mempr.web import Endpoint, HttpError  # noqa: E402


class handler(Endpoint):  # noqa: N801 (the name Vercel looks for)
    def get(self, query):
        try:
            return recall_entity(ROOT / "memory", query.get("entity"))
        except BadRequest as error:
            raise HttpError(error.status, str(error)) from error

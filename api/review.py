"""POST /api/review: `mempr check` on a draft from the page, against the committed memory.

Body: {"draft": {...}, "merged": [{...}, ...]} with drafts shaped as in
site/data/playground-cases.json. Returns the verdict, the findings and the Markdown comment
the GitHub check would post.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mempr.playground import BadDraft, review_request  # noqa: E402
from mempr.rules import load_rules  # noqa: E402
from mempr.web import Endpoint, HttpError  # noqa: E402


def rules():
    extra = ROOT / "memory-rules.yaml"
    return load_rules(extra.read_text() if extra.exists() else None)


class handler(Endpoint):  # noqa: N801 - the name Vercel's Python runtime looks for
    def post(self, body):
        try:
            return review_request(body, ROOT / "memory", rules())
        except BadDraft as error:
            raise HttpError(422, str(error)) from error

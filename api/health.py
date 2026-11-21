"""GET /api/health: Relate opens the committed memory on the server.

Returns the Relate version and how many facts the memory holds. The vault is copied to the
function's temporary folder first, because Relate writes sidecar files next to the notes and
the deployed files are read-only.
"""

from __future__ import annotations

import sys
import time
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mempr.vault import open_vault, rows  # noqa: E402
from mempr.web import Endpoint  # noqa: E402


def health(vault: Path = ROOT / "memory") -> dict:
    started = time.perf_counter()
    with open_vault(vault) as conn:
        facts = rows(conn, "MATCH (f:Fact) RETURN count(f) AS facts")[0]["facts"]
        notes = rows(conn, "MATCH (n) RETURN count(n) AS notes")[0]["notes"]
    return {
        "ok": True,
        "relate": metadata.version("relate-db"),
        "facts": facts,
        "notes": notes,
        "opened_in_ms": round((time.perf_counter() - started) * 1000, 1),
    }


class handler(Endpoint):  # noqa: N801 - the name Vercel's Python runtime looks for
    def get(self, query):
        return health()

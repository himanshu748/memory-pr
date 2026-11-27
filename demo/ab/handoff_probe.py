"""Run the read operations and log accesses for the pinned runtime handoff trial."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

SOURCES = {
    "fastapi": {
        "url": (
            "https://github.com/fastapi/fastapi/blob/"
            "05e6df7e906c3decba377c37548019973f40bf15/pyproject.toml"
        ),
        "n": 12,
        "line": 'requires-python = ">=3.11"',
    },
    "n8n": {
        "url": (
            "https://github.com/n8n-io/n8n/blob/"
            "327518f80bea779625523fcdb9562308680e3ae4/package.json"
        ),
        "n": 6,
        "line": '"node": ">=24.0.0",',
    },
    "tokio-macros": {
        "url": (
            "https://github.com/tokio-rs/tokio/blob/"
            "675ba8164071a6eee3dd1f3cb4fbdc1d3664396e/tokio-macros/Cargo.toml"
        ),
        "n": 9,
        "line": 'rust-version = "1.85"',
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("recall")
    source = commands.add_parser("source")
    source.add_argument("case", choices=SOURCES)
    args = parser.parse_args()

    access = {"type": args.action}
    if args.action == "source":
        access["case"] = args.case
    with Path("reads.jsonl").open("a", encoding="utf-8") as log:
        print(json.dumps(access), file=log)

    if args.action == "source":
        print(json.dumps({"id": args.case, **SOURCES[args.case]}))
        return

    for case in SOURCES:
        query = f"""
MATCH (f:Fact)-[:ABOUT]->(e:Entity {{name: {json.dumps(case)}}})
WHERE f.status = 'active'
  AND NOT EXISTS {{ MATCH (n:Fact)-[:SUPERSEDES]->(f) WHERE n.status = 'active' }}
OPTIONAL MATCH (f)-[:SOURCE]->(s:Source)
RETURN f.predicate AS predicate, f.value AS value, s.uri AS source
ORDER BY predicate
"""
        result = subprocess.run(
            ["mempr", "query", query], capture_output=True, text=True, check=True
        )
        print(case, result.stdout.strip())


if __name__ == "__main__":
    main()

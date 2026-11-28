"""Replay one FastAPI handoff against a local Git repository over MCP stdio."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ENTITY = "fastapi"
PREDICATE = "python-requires"
MANIFEST = "https://github.com/fastapi/fastapi/blob/{}/pyproject.toml"
OLD = MANIFEST.format("cbe5bdb85f393712e5176cfa27275781ffcc07c0")
NEW = MANIFEST.format("05e6df7e906c3decba377c37548019973f40bf15")


async def replay(root):
    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Replay")
    git("config", "user.email", "replay@example.invalid")
    cli = [sys.executable, "-m", "mempr.cli"]
    old = (
        subprocess.check_output(
            [
                *cli,
                "--repo",
                str(root),
                "add-fact",
                "--entity",
                ENTITY,
                "--predicate",
                PREDICATE,
                "--value",
                ">=3.8",
                "--source",
                OLD,
                "--agent",
                "maintainer",
            ],
            text=True,
        )
        .strip()
        .removeprefix("Fact/")
    )
    git("add", "memory")
    git("commit", "-qm", "Seed historical requirement")
    params = StdioServerParameters(
        command=sys.executable,
        env=dict(os.environ),
        args=[*cli[1:], "--repo", str(root), "mcp", "--base", "main", "--read-ref", "main"],
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()

        async def call(tool, **arguments):
            result = await session.call_tool(tool, arguments)
            if result.isError:
                raise RuntimeError(result.content[0].text)
            return json.loads(result.content[0].text)

        branch = "agent/runtime-update"
        proposal = await call(
            "propose_fact",
            entity=ENTITY,
            predicate=PREDICATE,
            value=">=3.11",
            agent="maintainer",
            source=NEW,
            supersedes=[old],
            branch=branch,
        )
        print(
            json.dumps({"tool": "propose_fact", "branch": branch, "blocked": proposal["blocked"]})
        )
        assert proposal["blocked"] is False
        git("checkout", "-q", branch)

        async def observe(phase, expected, **arguments):
            result = await call("recall", entity=ENTITY, **arguments)
            values = [f["value"] for f in result["facts"] if f["predicate"] == PREDICATE]
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "tool": "recall",
                        "arguments": arguments,
                        "entity": ENTITY,
                        "values": values,
                    }
                )
            )
            assert values == [expected]

        await observe("proposal checked out", ">=3.8")
        await observe("proposal checked out", ">=3.11", ref=branch)
        git("checkout", "-q", "main")
        git("merge", "--ff-only", branch)
        await observe("after local fast-forward", ">=3.11")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(replay(Path(directory)))

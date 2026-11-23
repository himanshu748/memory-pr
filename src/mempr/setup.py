"""`mempr setup`: wire the memory into a coding agent, and the hook that recalls it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from . import memory
from .vault import open_vault, rows

AGENTS = ("claude", "cursor", "codex")
UVX = ["uvx", "--from", "git+https://github.com/himanshu748/memory-pr", "mempr"]
EDITS = "Edit|MultiEdit|Write"


def base_command() -> list[str]:
    found = shutil.which("mempr")
    return [found] if found else list(UVX)


def _merge(path: Path, update) -> None:
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")


def _server(data: dict, cmd: list[str], repo: Path, vault: str) -> None:
    args = cmd[1:] + ["--repo", str(repo), "--vault", vault, "mcp"]
    data.setdefault("mcpServers", {})["memory"] = {"command": cmd[0], "args": args}


def _ours(handler: dict) -> bool:
    command = handler.get("command", "")
    return "mempr" in command and command.endswith('"hook"')


def _hook(data: dict, cmd: list[str], repo: Path, vault: str) -> None:
    line = " ".join(json.dumps(c) for c in cmd + ["--repo", str(repo), "--vault", vault, "hook"])
    groups = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    groups[:] = [g for g in groups if not any(_ours(h) for h in g.get("hooks", []))]
    groups.append({"matcher": EDITS, "hooks": [{"type": "command", "command": line}]})


def setup(repo: Path, vault: str, agents: list[str], cmd: list[str] | None = None) -> list[str]:
    """Write each agent's config into `repo`; returns what was done, one line each."""
    repo, cmd, done = repo.resolve(), cmd or base_command(), []
    skill = (Path(__file__).with_name("SKILL.md")).read_text("utf-8")
    if "claude" in agents:
        _merge(repo / ".mcp.json", lambda d: _server(d, cmd, repo, vault))
        _merge(repo / ".claude" / "settings.json", lambda d: _hook(d, cmd, repo, vault))
        target = repo / ".claude" / "skills" / "memory-pr" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill, "utf-8")
        done += [
            "Claude Code: MCP server in .mcp.json",
            "Claude Code: recall hook before every edit in .claude/settings.json",
            "Claude Code: skill in .claude/skills/memory-pr/SKILL.md",
        ]
    if "cursor" in agents:
        _merge(repo / ".cursor" / "mcp.json", lambda d: _server(d, cmd, repo, vault))
        done.append("Cursor: MCP server in .cursor/mcp.json")
    if "codex" in agents:
        args = " ".join(cmd + ["--repo", str(repo), "--vault", vault, "mcp"])
        done.append(f"Codex: run `codex mcp add memory -- {args}` (Codex keeps it in ~/.codex)")
    return done


def _path(payload: dict[str, Any]) -> str:
    tool = payload.get("tool_input") or {}
    return str(tool.get("file_path") or tool.get("path") or tool.get("notebook_path") or "")


def recall_for_edit(vault: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    """What the memory says about the entities a file to be edited belongs to, if anything."""
    path = _path(payload)
    if not path or not vault.is_dir():
        return None
    parts = {p.lower() for p in PurePosixPath(path.replace("\\", "/")).parts}
    if "memory" in parts:
        return None
    with open_vault(vault) as conn:
        names = [r["name"] for r in rows(conn, "MATCH (e:Entity) RETURN e.name AS name")]
    hits = sorted(n for n in names if isinstance(n, str) and n.lower() in parts)
    lines = []
    for entity in hits:
        for f in memory.recall(vault, entity):
            sources = ", ".join(f["sources"]) or "no source"
            lines.append(f"- {entity} {f['predicate']} = {f['value']} [{sources}]")
    if not lines:
        return None
    text = (
        f"Shared memory (memory-pr, reviewed facts) about what {path} belongs to:\n"
        + "\n".join(lines)
        + "\nIf this edit changes one of these, propose the new fact with its source "
        "(propose_fact, or `mempr add-fact` on a branch) so the memory stays true."
    )
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}

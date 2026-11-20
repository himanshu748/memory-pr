"""MCP server: lets a coding agent recall the shared memory and change it by pull request.

Reads never touch the checkout. Writes happen in a temporary git worktree on their own
branch, so the branch that is checked out, and the base branch, never move.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import gitutil, memory
from .check import review
from .report import render
from .rules import load_rules
from .vault import GENERATED, open_vault, rows

ROW_LIMIT = 200
PROTECTED_BRANCHES = {"main", "master"}
WRITE_CLAUSE = re.compile(
    r"(?<![.\w])(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|LOAD\s+CSV)(?!\w)", re.I
)
QUOTED = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`|//[^\n]*|/\*.*?\*/", re.S)

INSTRUCTIONS = """\
Shared project memory, reviewed like code. It is a graph of facts kept as Markdown in git:
(:Fact {predicate, value, status})-[:ABOUT]->(:Entity), -[:SOURCE]->(:Source {uri}),
-[:ASSERTED_BY]->(:Agent), -[:SUPERSEDES]->(:Fact), -[:DERIVED_FROM]->(:Fact).

- Before you assume something about the project, call `recall`. When it says nothing is
  known, do not guess: read the real source, then `propose_fact` with that source.
- You cannot write to the memory directly. `propose_fact` and `retract_fact` commit to a
  new branch and return the review of that branch. A blocked review tells you what to fix;
  pass the same `branch` again to add the fix to the same proposal.
- When a new value replaces an old one, name the old fact in `supersedes`, and retract or
  replace the facts that were derived from it.
"""


@dataclass(frozen=True)
class Settings:
    repo: Path
    vault: str = "memory"
    base: str = "origin/main"
    rules: str = "memory-rules.yaml"
    remote: str = "origin"


def is_read_only(cypher: str) -> bool:
    return not WRITE_CLAUSE.search(QUOTED.sub(" ", cypher))


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


@contextmanager
def _vault_at(cfg: Settings, ref: str | None) -> Iterator[Path]:
    """The vault folder in the working tree, or a temporary export of it at `ref`."""
    if ref is None:
        path = cfg.repo / cfg.vault
        if not path.is_dir():
            raise ValueError(f"no memory folder at {path}")
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="mempr-ref-") as tmp:
        yield gitutil.export_tree(cfg.repo, gitutil.rev_parse(cfg.repo, ref), cfg.vault, Path(tmp))


def _branch_exists(repo: Path, branch: str) -> bool:
    ref = f"refs/heads/{branch}"
    return gitutil.git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def _review(cfg: Settings, head: str, base: str) -> dict[str, Any]:
    # Rules come from the base branch, so a proposal cannot switch off its own review.
    rules = load_rules(gitutil.read_file(cfg.repo, base, cfg.rules))
    result = review(cfg.repo, base, head, cfg.vault, rules)
    return {**result.to_dict(), "report": render(result)}


def _commit_on_branch(
    cfg: Settings,
    *,
    branch: str | None,
    base: str,
    agent: str,
    topic: str,
    message: str,
    write: Callable[[Path], Any],
) -> tuple[str, str, Any]:
    """Run `write(vault)` in a temporary worktree of `branch` and commit the result.

    Returns (branch, commit, whatever `write` returned). A branch created here is deleted
    again when the write fails or changes nothing.
    """
    if memory.slug(agent) != agent or not agent:
        raise ValueError(f"agent {agent!r} must be lowercase letters, digits and hyphens")
    branch = branch or f"agent/{agent}-{memory.slug(topic)[:48].strip('-')}-{int(time.time())}"
    if branch in PROTECTED_BRANCHES or branch == base.removeprefix(f"{cfg.remote}/"):
        raise ValueError(f"refusing to write on {branch!r}: memory changes go through a branch")
    if gitutil.git(cfg.repo, "check-ref-format", "--branch", branch, check=False).returncode:
        raise ValueError(f"{branch!r} is not a valid branch name")
    if branch == gitutil.git(cfg.repo, "branch", "--show-current").stdout.strip():
        raise ValueError(f"{branch!r} is checked out here; propose on another branch")
    created = not _branch_exists(cfg.repo, branch)
    tmp = tempfile.mkdtemp(prefix="mempr-propose-")
    tree = Path(tmp) / "tree"
    committed = False
    try:
        if created:
            gitutil.git(cfg.repo, "worktree", "add", "-q", "-b", branch, str(tree), base)
        else:
            gitutil.git(cfg.repo, "worktree", "add", "-q", str(tree), branch)
        try:
            written = write(tree / cfg.vault)
            exclude = [f":(exclude,glob){cfg.vault}/**/{name}" for name in sorted(GENERATED)]
            exclude += [
                f":(exclude,glob){cfg.vault}/**/{p}" for p in (".relate/**", ".cypherlite.*")
            ]
            gitutil.git(tree, "add", "-A", "--", cfg.vault, *exclude)
            if gitutil.git(tree, "diff", "--cached", "--quiet", check=False).returncode == 0:
                raise ValueError(f"nothing to change: {branch} already says this")
            gitutil.git(
                tree,
                "-c",
                f"user.name={agent}-agent",
                "-c",
                f"user.email={agent}@agents.invalid",
                "commit",
                "-q",
                "-m",
                message,
            )
            committed = True
            commit = gitutil.rev_parse(tree, "HEAD")
        finally:
            gitutil.git(cfg.repo, "worktree", "remove", "--force", str(tree), check=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        gitutil.git(cfg.repo, "worktree", "prune", check=False)
        if created and not committed:
            gitutil.git(cfg.repo, "branch", "-q", "-D", branch, check=False)
    return branch, commit, written


def _open_pull_request(cfg: Settings, branch: str, base: str, title: str, body: str) -> dict:
    """Push the branch and open a pull request with `gh`. Failures are returned, not raised:
    the commit already exists and the agent can still push it another way."""
    push = gitutil.git(cfg.repo, "push", "-q", "-u", cfg.remote, branch, check=False)
    if push.returncode != 0:
        return {"pushed": False, "error": f"git push failed: {push.stderr.strip()}"}
    try:
        proc = subprocess.run(
            ["gh", "pr", "create", "--base", base.removeprefix(f"{cfg.remote}/")]
            + ["--head", branch, "--title", title, "--body", body],
            cwd=cfg.repo,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {"pushed": True, "error": "gh is not installed; open the pull request by hand"}
    if proc.returncode != 0:
        return {"pushed": True, "error": f"gh pr create failed: {proc.stderr.strip()}"}
    return {"pushed": True, "url": proc.stdout.strip().splitlines()[-1]}


def _proposal(
    cfg: Settings,
    *,
    branch: str | None,
    base: str | None,
    agent: str,
    topic: str,
    summary: str,
    open_pull_request: bool,
    write: Callable[[Path], Any],
) -> dict[str, Any]:
    base = base or cfg.base
    branch, commit, written = _commit_on_branch(
        cfg,
        branch=branch,
        base=base,
        agent=agent,
        topic=topic,
        message=f"memory: {summary}",
        write=write,
    )
    result = _review(cfg, branch, base)
    out = {
        "fact": written,
        "branch": branch,
        "commit": commit,
        "base": base,
        "blocked": result["blocked"],
        "review": result,
        "next": (
            "The review blocks this change. Read review.report, then fix it on the same "
            "branch by passing branch again."
            if result["blocked"]
            else "The review passes. The fact becomes shared memory when the branch is merged."
        ),
    }
    if open_pull_request:
        body = f"Proposed by agent `{agent}` through the mempr MCP server.\n\n{summary}"
        out["pull_request"] = _open_pull_request(cfg, branch, base, f"memory: {summary}", body)
    return out


def build_server(cfg: Settings) -> FastMCP:
    server = FastMCP("mempr", instructions=INSTRUCTIONS)

    @server.tool()
    def recall(entity: str, ref: str | None = None) -> dict[str, Any]:
        """Current facts about one entity (a service, a tool, a person), each with the
        sources and agents behind it. Retracted and replaced facts are left out.

        `ref` reads the memory at a git revision (for example `origin/main`) instead of the
        working tree.
        """
        with _vault_at(cfg, ref) as vault:
            facts = _plain(memory.recall(vault, entity))
        if not facts:
            return {
                "entity": entity,
                "known": False,
                "facts": [],
                "answer": (
                    f"Nothing known about {entity}. Do not guess: check the real source, "
                    "then record what you find with propose_fact."
                ),
            }
        lines = [
            f"{entity} {f['predicate']} = {f['value']} "
            f"[source: {', '.join(f['sources']) or 'none'}; by {', '.join(f['agents']) or '?'}]"
            for f in facts
        ]
        return {"entity": entity, "known": True, "facts": facts, "answer": "\n".join(lines)}

    @server.tool()
    def query(cypher: str, ref: str | None = None) -> dict[str, Any]:
        """Run a read-only Cypher query on the memory graph and return its rows.

        Labels: Fact, Entity, Source, Agent, Predicate. Relationships: ABOUT, SOURCE,
        ASSERTED_BY, SUPERSEDES, DERIVED_FROM. Writing clauses are refused; use
        propose_fact and retract_fact to change the memory.
        """
        if not is_read_only(cypher):
            raise ValueError(
                "query is read-only: CREATE, MERGE, SET, DELETE, REMOVE, DROP, FOREACH and "
                "LOAD CSV are refused. Use propose_fact or retract_fact to change the memory."
            )
        # A throwaway copy of the vault: even a write the check missed cannot reach the files.
        with _vault_at(cfg, ref) as vault, open_vault(vault) as conn:
            found = _plain(rows(conn, cypher))
        return {
            "rows": found[:ROW_LIMIT],
            "row_count": len(found),
            "truncated": len(found) > ROW_LIMIT,
        }

    @server.tool()
    def propose_fact(
        entity: str,
        predicate: str,
        value: str,
        source: str,
        agent: str,
        statement: str = "",
        supersedes: list[str] | None = None,
        derived_from: list[str] | None = None,
        branch: str | None = None,
        base: str | None = None,
        open_pull_request: bool = False,
    ) -> dict[str, Any]:
        """Propose one fact: commit it on a branch (never on the base branch or the branch
        that is checked out) and return the review of that branch against the base.

        `source` is where you read it (a file path or URL) and is required. `supersedes` and
        `derived_from` take fact names as returned by recall. Pass `branch` to add to an
        earlier proposal; leave it out to start a new one. `open_pull_request` also pushes
        the branch and opens a pull request with `gh`.
        """

        def write(vault: Path) -> str:
            return memory.add_fact(
                vault,
                entity=entity,
                predicate=predicate,
                value=value,
                agent=agent,
                source=source,
                statement=statement,
                supersedes=supersedes,
                derived_from=derived_from,
            )

        return _proposal(
            cfg,
            branch=branch,
            base=base,
            agent=agent,
            topic=f"{entity}-{predicate}",
            summary=f"{entity} {predicate} = {value} (source: {source})",
            open_pull_request=open_pull_request,
            write=write,
        )

    @server.tool()
    def retract_fact(
        fact: str,
        agent: str,
        reason: str = "",
        branch: str | None = None,
        base: str | None = None,
        open_pull_request: bool = False,
    ) -> dict[str, Any]:
        """Propose retracting a fact (by its name, as returned by recall): commit the
        retraction on a branch and return the review of that branch against the base.
        Facts derived from a retracted fact must be retracted or replaced too.
        """

        def write(vault: Path) -> str:
            memory.retract(vault, fact)
            return fact

        return _proposal(
            cfg,
            branch=branch,
            base=base,
            agent=agent,
            topic=f"retract-{fact}",
            summary=f"retract {fact}" + (f" ({reason})" if reason else ""),
            open_pull_request=open_pull_request,
            write=write,
        )

    @server.tool(name="review")
    def review_branch(head: str, base: str | None = None) -> dict[str, Any]:
        """Review merging `head` (a branch or revision) into `base` as graph facts: what it
        adds and removes, what the merge would lose, and which rules it breaks. `blocked`
        says whether the change may merge; `report` is the same text the pull request gets.
        """
        return _review(cfg, head, base or cfg.base)

    return server


def serve(cfg: Settings) -> None:
    build_server(cfg).run("stdio")

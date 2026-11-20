"""Drive `mempr mcp` the way an agent does: a subprocess speaking MCP over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mempr.mcp_server import _pull_request_target, is_read_only

DB = "payments-api--database--postgres-15"
TOOL = "payments-api--migration-tool--pg-migrate"


class Agent:
    """Runs one MCP session per call batch against the repo's server."""

    def __init__(self, repo, env: dict[str, str] | None = None):
        self.repo = repo
        self.params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mempr.cli", "--repo", str(repo.path), "mcp", "--base", "main"],
            env={**os.environ, **(env or {})},
        )

    def session(self, script):
        async def main():
            async with stdio_client(self.params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    return await script(session, init)

        return asyncio.run(asyncio.wait_for(main(), 120))

    def call(self, tool: str, **arguments):
        """Call one tool; returns (is_error, decoded JSON result or error text)."""

        async def script(session, _init):
            result = await session.call_tool(tool, arguments)
            text = result.content[0].text
            return result.isError, text if result.isError else json.loads(text)

        return self.session(script)

    def ok(self, tool: str, **arguments):
        is_error, value = self.call(tool, **arguments)
        assert not is_error, value
        return value


@pytest.fixture
def agent(repo) -> Agent:
    return Agent(repo)


def state(repo) -> tuple[str, str, str]:
    return repo.git("rev-parse", "main"), repo.git("branch", "--show-current"), status(repo)


def status(repo) -> str:
    return repo.git("status", "--porcelain", "--untracked-files=all")


def test_server_lists_its_tools_and_instructions(agent):
    async def script(session, init):
        return init, await session.list_tools()

    init, listed = agent.session(script)
    assert init.serverInfo.name == "mempr"
    assert "do not guess" in init.instructions
    assert {t.name for t in listed.tools} == {
        "recall",
        "query",
        "propose_fact",
        "retract_fact",
        "review",
    }
    propose = next(t for t in listed.tools if t.name == "propose_fact")
    assert {"entity", "predicate", "value", "source", "agent"} <= set(
        propose.inputSchema["required"]
    )


def test_recall_returns_current_facts_with_sources(agent, repo):
    before = state(repo)
    out = agent.ok("recall", entity="payments-api")
    assert out["known"] is True
    facts = {f["predicate"]: f for f in out["facts"]}
    assert facts["database"]["value"] == "postgres-15"
    assert facts["database"]["sources"] == ["docs-payments-api-md"]
    assert facts["database"]["agents"] == ["scout"]
    assert "payments-api database = postgres-15 [source: docs-payments-api-md" in out["answer"]
    assert state(repo) == before  # reading leaves no Relate files behind


def test_recall_of_an_unknown_entity_says_do_not_guess(agent):
    out = agent.ok("recall", entity="billing-gateway")
    assert out["known"] is False
    assert out["facts"] == []
    assert out["answer"].startswith("Nothing known about billing-gateway. Do not guess")


def test_query_runs_cypher_and_refuses_writes(agent, repo):
    before = state(repo)
    out = agent.ok(
        "query",
        cypher=(
            "MATCH (f:Fact)-[:DERIVED_FROM]->(r:Fact {name: '" + DB + "'}) RETURN f.name AS fact"
        ),
    )
    assert out == {"rows": [{"fact": TOOL}], "row_count": 1, "truncated": False}

    is_error, text = agent.call("query", cypher="MATCH (f:Fact) SET f.status = 'retracted'")
    assert is_error and "read-only" in text
    is_error, text = agent.call("query", cypher="CREATE (:Entity {name: 'x'})")
    assert is_error and "read-only" in text
    assert state(repo) == before
    assert agent.ok("recall", entity="payments-api")["known"] is True


def test_read_only_check_ignores_strings_and_property_names():
    assert is_read_only("MATCH (f:Fact) WHERE f.value = 'CREATE SET' RETURN f.set, f.name")
    assert is_read_only("MATCH (f:Fact) RETURN f.name AS created // DELETE nothing")
    assert not is_read_only("MATCH (f:Fact) DETACH DELETE f")
    assert not is_read_only("match (f:Fact) set f.x = 1")
    assert not is_read_only("MERGE (:Entity {name: 'x'})")


def test_propose_fact_commits_on_a_new_branch_and_passes_review(agent, repo):
    before = state(repo)
    out = agent.ok(
        "propose_fact",
        entity="ledger-worker",
        predicate="queue",
        value="kafka",
        source="services/ledger-worker/service.yaml",
        agent="builder",
    )
    assert out["fact"] == "ledger-worker--queue--kafka"
    assert out["branch"].startswith("agent/builder-ledger-worker-queue-")
    assert out["blocked"] is False
    assert ["node", "Fact/ledger-worker--queue--kafka"] in out["review"]["added"]

    # main, the checked-out branch and the working tree did not move
    assert state(repo) == before
    assert repo.git("worktree", "list").count("\n") == 0
    # the fact is one commit on the new branch, by the agent, and holds only notes
    assert repo.git("rev-parse", out["branch"]) == out["commit"]
    assert repo.git("rev-parse", f"{out['branch']}~1") == before[0]
    assert repo.git("log", "-1", "--format=%an", out["branch"]) == "builder-agent"
    changed = repo.git("diff", "--name-only", "main", out["branch"]).splitlines()
    assert "memory/Fact/ledger-worker--queue--kafka.md" in changed
    assert all(p.startswith("memory/") and ".relate" not in p for p in changed)
    assert not any(Path(p).name in ("index.md", "log.md") for p in changed)

    # not in memory until merged; visible when reading the branch
    assert agent.ok("recall", entity="ledger-worker")["known"] is False
    on_branch = agent.ok("recall", entity="ledger-worker", ref=out["branch"])
    assert on_branch["facts"][0]["value"] == "kafka"


def test_contradicting_proposal_is_blocked_then_fixed_on_the_same_branch(agent, repo):
    bad = agent.ok(
        "propose_fact",
        entity="payments-api",
        predicate="database",
        value="mysql-8",
        source="docs/migration.md",
        agent="builder",
    )
    assert bad["blocked"] is True
    assert [f["rule"] for f in bad["review"]["findings"]] == ["contradiction"]
    assert "Path:" in bad["review"]["report"]
    assert "same branch" in bad["next"]

    # say it replaces the old fact: now the fact derived from the old one is stale
    fix = agent.ok(
        "propose_fact",
        entity="payments-api",
        predicate="database",
        value="mysql-8",
        source="docs/migration.md",
        agent="builder",
        supersedes=[DB],
        branch=bad["branch"],
    )
    assert fix["branch"] == bad["branch"]
    assert [f["rule"] for f in fix["review"]["findings"]] == ["stale-derivation"]

    done = agent.ok(
        "retract_fact", fact=TOOL, agent="builder", reason="database moved", branch=bad["branch"]
    )
    assert done["blocked"] is False
    assert repo.git("rev-list", "--count", f"main..{bad['branch']}") == "3"
    assert "retract" in repo.git("log", "-1", "--format=%s", bad["branch"])

    again = agent.ok("review", head=bad["branch"])
    assert again["blocked"] is False
    assert again["head"] == done["commit"]


def test_retract_fact_on_its_own_branch_is_reviewed(agent, repo):
    before = state(repo)
    out = agent.ok("retract_fact", fact=DB, agent="builder")
    assert out["blocked"] is True  # pg-migrate still rests on it
    assert [f["rule"] for f in out["review"]["findings"]] == ["stale-derivation"]
    assert state(repo) == before
    note = repo.git("show", f"{out['branch']}:memory/Fact/{DB}.md")
    assert "status: retracted" in note


def test_failed_writes_leave_no_branch_behind(agent, repo):
    branches = repo.git("branch", "--list")
    cases = [
        ("retract_fact", {"fact": "no-such-fact", "agent": "builder"}, "no fact named"),
        (
            "propose_fact",
            {
                "entity": "Payments API",
                "predicate": "database",
                "value": "x",
                "source": "docs/x.md",
                "agent": "builder",
            },
            "lowercase",
        ),
        (
            "propose_fact",  # already in memory, word for word
            {
                "entity": "checkout-web",
                "predicate": "runtime",
                "value": "node-22",
                "source": "docs/checkout-web.md",
                "agent": "scout",
            },
            "nothing to change",
        ),
        (
            "propose_fact",
            {
                "entity": "checkout-web",
                "predicate": "port",
                "value": "3000",
                "source": "docs/checkout-web.md",
                "agent": "builder",
                "branch": "main",
            },
            "refusing to write on 'main'",
        ),
    ]
    for tool, arguments, expected in cases:
        is_error, text = agent.call(tool, **arguments)
        assert is_error and expected in text, text
    assert repo.git("branch", "--list") == branches
    assert repo.git("worktree", "list").count("\n") == 0
    assert status(repo) == ""


def test_review_tool_reports_a_branch_made_outside_the_server(agent, repo):
    repo.branch("agent/bad")
    repo.add("payments-api", "database", "mysql-8", agent="builder")
    repo.commit("learn database")
    repo.git("checkout", "-q", "main")
    out = agent.ok("review", head="agent/bad", base="main")
    assert out["blocked"] is True
    assert out["findings"][0]["rule"] == "contradiction"
    assert "(Entity/payments-api)<-[:ABOUT]-" in out["report"]


def test_open_pull_request_pushes_the_branch_and_calls_gh(repo, tmp_path):
    remote = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(remote))
    # fetch URL names a GitHub repository; pushes go to the local bare repository
    repo.git("remote", "add", "origin", "https://github.com/acme/project.git")
    repo.git("remote", "set-url", "--push", "origin", str(remote))
    repo.git("push", "-q", "origin", "main")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"  # stands in for the GitHub CLI: no network in tests
    gh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\necho https://example.invalid/pull/7\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    agent = Agent(repo, env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})

    out = agent.ok(
        "propose_fact",
        entity="ledger-worker",
        predicate="queue",
        value="kafka",
        source="services/ledger-worker/service.yaml",
        agent="builder",
        open_pull_request=True,
    )
    assert out["pull_request"] == {"pushed": True, "url": "https://example.invalid/pull/7"}
    pushed = repo.git("ls-remote", "--heads", str(remote), out["branch"])
    assert pushed.split()[0] == out["commit"]
    args = log.read_text().splitlines()
    assert args[:6] == ["pr", "create", "--base", "main", "--head", out["branch"]]
    assert "memory: ledger-worker queue = kafka" in args[7]
    assert args[-2:] == ["--repo", "github.com/acme/project"]  # the remote that was pushed to


def test_proposing_on_the_checked_out_branch_is_refused(agent, repo):
    repo.branch("agent/work")
    before = state(repo)
    is_error, text = agent.call("retract_fact", fact=TOOL, agent="builder", branch="agent/work")
    assert is_error and "checked out" in text
    assert state(repo) == before


def test_base_branch_cannot_be_written_under_another_name(agent, repo):
    repo.git("branch", "release", "main")
    repo.git("branch", "agent/base", "main")
    before = repo.git("for-each-ref", "refs/heads")
    fact = {
        "entity": "checkout-web",
        "predicate": "port",
        "value": "3000",
        "source": "docs/checkout-web.md",
        "agent": "builder",
    }
    for branch, base in [
        ("release", "refs/heads/release"),
        ("release", "main"),  # an existing branch that is not a proposal
        ("agent/base", "refs/heads/agent/base"),
        ("agent/base", "heads/agent/base"),
    ]:
        is_error, text = agent.call("propose_fact", **fact, branch=branch, base=base)
        assert is_error and "refusing to write" in text, text
    assert repo.git("for-each-ref", "refs/heads") == before


def test_proposals_in_the_same_second_get_separate_branches(agent, repo):
    async def script(session, _init):
        out = []
        for value in ("3000", "8080"):
            result = await session.call_tool(
                "propose_fact",
                {
                    "entity": "checkout-web",
                    "predicate": "port",
                    "value": value,
                    "source": "docs/checkout-web.md",
                    "agent": "builder",
                },
            )
            assert not result.isError, result.content[0].text
            out.append(json.loads(result.content[0].text))
        return out

    first, second = agent.session(script)
    if first["branch"].rsplit("-", 1)[-1] == second["branch"].split("-")[-2]:
        assert second["branch"] == first["branch"] + "-2"  # same second: suffixed
    assert first["branch"] != second["branch"]
    for out in (first, second):  # each is its own one-commit proposal, so neither is blocked
        assert repo.git("rev-list", "--count", f"main..{out['branch']}") == "1"
        assert out["blocked"] is False


def test_pull_request_goes_to_the_fetch_repository_with_a_fork_as_head(repo):
    repo.git("remote", "add", "origin", "git@github.com:acme/project.git")
    assert _pull_request_target(repo.path, "origin", "agent/x") == (
        "github.com/acme/project",
        "agent/x",
    )
    repo.git("remote", "set-url", "--push", "origin", "https://github.com/builder-bot/project")
    assert _pull_request_target(repo.path, "origin", "agent/x") == (
        "github.com/acme/project",
        "builder-bot:agent/x",
    )
    repo.git("remote", "add", "local", "../somewhere.git")
    assert _pull_request_target(repo.path, "local", "agent/x") == (None, "agent/x")

# Use it from an agent

[Back to README](../README.md)

One command, run in the repository that holds `memory/`:

```bash
mempr setup            # or: --agent claude / cursor / codex
```

It adds the MCP server below to `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor),
copies the [skill](#a-skill-for-coding-agents) into `.claude/skills/`, prints the
`codex mcp add` line for Codex, and adds a Claude Code `PreToolUse` hook: before an agent
edits a file, `mempr hook` recalls what the memory knows about the entity the file belongs
to (`services/payments-api/db.go` gets the `payments-api` facts, each with its source) and
hands it to the agent as context. The hook never blocks an edit, and says nothing when
nothing is known. Existing entries in those files are kept; running it twice changes
nothing.

To configure it by hand instead:

`mempr mcp` is an [MCP](https://modelcontextprotocol.io) server over stdio, so Claude Code,
Cursor, Devin or any other MCP client can use the memory while it works. Add it to the
client's MCP config (`.mcp.json` for Claude Code, `.cursor/mcp.json` for Cursor), with the
path of the repository that holds `memory/`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/himanshu748/memory-pr",
        "mempr", "--repo", "/absolute/path/to/your/repo", "mcp"
      ]
    }
  }
}
```

With `mempr` already installed, the command is `mempr --repo /absolute/path/to/your/repo mcp`
(Claude Code: `claude mcp add memory -- mempr --repo "$PWD" mcp`).

| Tool | What the agent gets |
| --- | --- |
| `recall` | The current facts about an entity, each with its sources and authors. For an entity the memory does not know, the answer is "Nothing known ... Do not guess", not an empty list to fill in. |
| `search` | A keyword match (entity, predicate, value, statement, source path) plus the facts up to 6 hops along `DERIVED_FROM`. Use it when the entity name is not known. No model and no API key. The answer includes the Cypher. |
| `query` | The rows of a read-only Cypher query. Writing clauses are refused, and the query runs on a throwaway copy of the files. |
| `propose_fact` | One fact, written with `mempr.memory.add_fact` and committed on a new branch, plus the review of that branch. `open_pull_request: true` also pushes it and opens the pull request with `gh`. |
| `retract_fact` | The same for a retraction. |
| `review` | The review of any branch against a base: the result of `mempr check` as JSON, and the comment text. |

An agent cannot write to the memory directly. A proposal is committed on an `agent/*`
branch in a temporary git worktree, so the branch you have checked out, your working tree and `main` do not move.
When the review blocks a proposal, the agent reads why and sends the fix with the same
`branch`. With `open_pull_request: true`, the fix is pushed and the tool returns the
existing open pull request's URL. The fact becomes shared memory when the pull request
is merged.
`--base` sets the branch proposals start from and are reviewed against (default
`origin/main`).

### A skill for coding agents

[`.claude/skills/memory-pr/SKILL.md`](../.claude/skills/memory-pr/SKILL.md) is an
[Agent Skill](https://agentskills.io) that teaches a coding agent the habit: recall before
assuming, never edit `memory/` on the main branch, propose every fact with its source on
a branch, and fix what the review blocks. It works with the MCP server or with the plain
`mempr` commands. Copy the folder into your repository's `.claude/skills/` (Claude Code)
or wherever your agent reads skills from.

## Use it in your own repository

1. Keep your memory as Markdown notes under `memory/` (or pass `vault:`), one note per
   fact, in the [graph shape](how-it-works.md#the-graph). `mempr add-fact` writes them.
2. Add this workflow as `.github/workflows/memory-review.yml`:

   ```yaml
   name: memory-review
   on: pull_request
   permissions:
     contents: read
     pull-requests: write
   jobs:
     memory-review:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
           with:
             fetch-depth: 0
         - uses: himanshu748/memory-pr@v1
   ```

   The action installs `mempr`, reviews the pull request against its base branch, posts
   the review as one comment that it keeps up to date, and fails when the merge would
   damage the graph. Inputs: `base`, `head`, `vault`, `rules` (an extra rules file, read
   from the base branch), `comment` and `python-version`.
3. In the branch protection for your main branch, require the `memory-review` check.

This repository's own [`memory-review.yml`](../.github/workflows/memory-review.yml) does the
same steps by hand, because it also installs `mempr` from the base branch and reviews
pull requests that the Actions token opened.

## For judges

| Criterion | Where to see it |
| --- | --- |
| Relate and connected context (30) | The store is the Markdown vault [`memory/`](../memory/), opened by Relate in `directory` mode; facts link to the entity they are `ABOUT`, the agent they are `ASSERTED_BY`, their `SOURCE`, and the facts they `SUPERSEDES` or are `DERIVED_FROM`. Every review rule is Cypher ([`default_rules.yaml`](../src/mempr/default_rules.yaml)), including a 1–6 hop contradiction and a retracted-source chain, and the check comment prints the query. `mempr search` is a keyword match plus that same hop. The rules run in CI, on the server and in the page (WebAssembly). [`bench/real-memory/`](../bench/real-memory/) is a second store: 590 real facts from 50 public repositories. |
| Functionality and completeness (25) | A required GitHub check ([`action.yml`](../action.yml)) that blocks pull requests [#15](https://github.com/himanshu748/memory-pr/pull/15) and passes [#6](https://github.com/himanshu748/memory-pr/pull/6); an [MCP server](#use-it-from-an-agent); a [backend](usage.md#backend); the [live page](https://memory-pr.vercel.app/), which reviews any public GitHub repository and can open a real pull request here. 253 tests run in CI. |
| Usefulness (20) | [On 50 popular public repositories](benchmarks.md#measured-on-50-real-repositories), 154 of the 328 facts an agent would have learned a year ago are wrong today; the check blocks all 154 until the new fact replaces the old one. The [A/B agent run](EVIDENCE.md#real-agent-run-ab) shows reviewed memory changing what an agent ships. |
| Documentation and reproducibility (15) | [`./quickstart.sh`](../quickstart.sh) serves the playground with no install. [`./test.sh`](../test.sh) creates a virtualenv and runs the Python tests and `node --test tests/js/*.test.mjs` (no `npm install`, no Docker, no API key). Both measurements are rerun by one command each, and their raw numbers are committed in [`docs/`](.). |
| Creativity (10) | The thing under review is the merge git calls clean. A new note about a different entity can contradict a fact three hops back, and the comment shows the Cypher that caught it ([why a text diff is not enough](how-it-works.md#why-a-text-diff-is-not-enough)). |

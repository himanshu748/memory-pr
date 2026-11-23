import io
import json
import shlex

from mempr import cli
from mempr.setup import recall_for_edit, setup

CMD = ["mempr"]


def test_setup_writes_each_agent_config_and_keeps_what_was_there(repo):
    servers = {"other": {"command": "x"}, "memory": {"type": "http", "url": "u", "env": {"A": "1"}}}
    (repo.path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    other = {"type": "command", "command": "python tools/mempr_audit.py --repo . hook"}
    claude = repo.path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [other]}]}})
    )
    done = setup(repo.path, "memory", ["claude", "cursor", "codex"], CMD)
    setup(repo.path, "memory", ["claude"], CMD)  # twice: still one hook

    mcp = json.loads((repo.path / ".mcp.json").read_text())
    assert set(mcp["mcpServers"]) == {"other", "memory"}
    assert mcp["mcpServers"]["memory"]["args"][-1] == "mcp"
    assert mcp["mcpServers"]["memory"]["env"] == {"A": "1"}
    assert "url" not in mcp["mcpServers"]["memory"]
    assert str(repo.path.resolve()) in mcp["mcpServers"]["memory"]["args"]
    cursor = json.loads((repo.path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["memory"]["args"] == mcp["mcpServers"]["memory"]["args"]
    hooks = json.loads((repo.path / ".claude" / "settings.json").read_text())["hooks"]
    assert hooks["PreToolUse"][0]["hooks"] == [other]
    assert len(hooks["PreToolUse"]) == 2
    assert hooks["PreToolUse"][1]["matcher"] == "Edit|MultiEdit|Write"
    assert shlex.split(hooks["PreToolUse"][1]["hooks"][0]["command"])[-1] == "hook"
    assert (repo.path / ".claude" / "skills" / "memory-pr" / "SKILL.md").is_file()
    assert any("codex mcp add memory" in line for line in done)


def test_packaged_skill_is_the_repository_skill():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    packaged = (root / "src" / "mempr" / "SKILL.md").read_text()
    assert packaged == (root / ".claude" / "skills" / "memory-pr" / "SKILL.md").read_text()


def test_hook_recalls_the_entity_a_file_belongs_to(repo):
    repo.add("payments-api", "database", "postgres-15")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "services/payments-api/db.go"}}
    out = recall_for_edit(repo.path / "memory", payload)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "payments-api database = postgres-15" in context


def test_hook_is_silent_when_nothing_is_known(repo, monkeypatch, capsys):
    repo.add("payments-api", "database", "postgres-15")
    assert recall_for_edit(repo.path / "memory", {"tool_input": {"file_path": "web/x.ts"}}) is None
    assert recall_for_edit(repo.path / "memory", {"tool_input": {}}) is None
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["--repo", str(repo.path), "hook"]) == 0
    assert capsys.readouterr().out == ""


def test_paths_with_spaces_stay_one_argument(tmp_path):
    repo = tmp_path / "my repo é"
    repo.mkdir()
    done = setup(repo, "memory", ["claude", "codex"], CMD)
    hooks = json.loads((repo / ".claude" / "settings.json").read_text())["hooks"]
    assert str(repo.resolve()) in shlex.split(hooks["PreToolUse"][0]["hooks"][0]["command"])
    codex = next(line for line in done if "codex mcp add" in line)
    assert str(repo.resolve()) in shlex.split(codex.split("`")[1])

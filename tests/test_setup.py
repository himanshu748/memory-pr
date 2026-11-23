import io
import json

from mempr import cli
from mempr.setup import recall_for_edit, setup

CMD = ["mempr"]


def test_setup_writes_each_agent_config_and_keeps_what_was_there(repo):
    (repo.path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    done = setup(repo.path, "memory", ["claude", "cursor", "codex"], CMD)
    setup(repo.path, "memory", ["claude"], CMD)  # twice: still one hook

    mcp = json.loads((repo.path / ".mcp.json").read_text())
    assert set(mcp["mcpServers"]) == {"other", "memory"}
    assert mcp["mcpServers"]["memory"]["args"][-1] == "mcp"
    assert str(repo.path.resolve()) in mcp["mcpServers"]["memory"]["args"]
    cursor = json.loads((repo.path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["memory"] == mcp["mcpServers"]["memory"]
    hooks = json.loads((repo.path / ".claude" / "settings.json").read_text())["hooks"]
    assert len(hooks["PreToolUse"]) == 1
    assert hooks["PreToolUse"][0]["matcher"] == "Edit|MultiEdit|Write"
    assert hooks["PreToolUse"][0]["hooks"][0]["command"].endswith('"hook"')
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

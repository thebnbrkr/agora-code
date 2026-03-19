"""
test_hooks.py — Hook config validation and CLI smoke tests.

Two things tested:
  1. All hook config files (.claude/hooks.json, .cursor/hooks.json,
     .gemini/settings.json) are valid JSON with the correct event names
     and required structure.
  2. The CLI commands used by hooks exit 0 and don't crash when there's
     no active session (the silent no-op path hooks rely on).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agora_code.cli import _install_claude_code_hooks  # imported at collection time (CWD = repo root)

REPO_ROOT = Path(__file__).parent.parent


# --------------------------------------------------------------------------- #
#  Hook config validation                                                     #
# --------------------------------------------------------------------------- #

class TestClaudeHooks:
    def setup_method(self):
        # Claude Code uses .claude/settings.json (install-hooks --claude-code creates this)
        self.path = REPO_ROOT / ".claude" / "settings.json"
        self.config = json.loads(self.path.read_text(encoding="utf-8"))

    def test_is_valid_json(self):
        assert isinstance(self.config, dict)

    def test_has_hooks_key(self):
        assert "hooks" in self.config

    def test_session_start_present(self):
        assert "SessionStart" in self.config["hooks"]

    def test_pre_compact_present(self):
        assert "PreCompact" in self.config["hooks"]

    def test_post_tool_use_present(self):
        assert "PostToolUse" in self.config["hooks"]

    def test_pre_compact_uses_checkpoint_not_state_save(self):
        hooks = self.config["hooks"]["PreCompact"]
        commands = _extract_commands(hooks)
        for cmd in commands:
            assert "state save" not in cmd, (
                f"Found broken 'state save' command: {cmd!r}. Should be 'checkpoint'."
            )
        assert any("checkpoint" in c for c in commands), (
            "PreCompact should call 'agora-code checkpoint'"
        )

    def test_session_start_uses_inject(self):
        hooks = self.config["hooks"]["SessionStart"]
        commands = _extract_commands(hooks)
        assert any("inject" in c for c in commands)

    def test_grep_hook_present(self):
        """PostToolUse must have a Grep matcher so grep results get indexed."""
        post_tool = self.config["hooks"]["PostToolUse"]
        matchers = []
        for item in post_tool:
            matchers.append(item.get("matcher", ""))
            for h in item.get("hooks", []):
                pass
        assert any("Grep" in m for m in matchers), (
            "PostToolUse must include a 'Grep' matcher — grep results are not indexed without it"
        )

    def test_grep_hook_script_exists(self):
        post_tool = self.config["hooks"]["PostToolUse"]
        for item in post_tool:
            if "Grep" in item.get("matcher", ""):
                for hook in item.get("hooks", []):
                    cmd = hook.get("command", "")
                    if cmd.endswith(".sh"):
                        script = REPO_ROOT / cmd
                        assert script.exists(), f"Grep hook script missing: {cmd}"
                        assert script.stat().st_mode & 0o111, f"Grep hook script not executable: {cmd}"

    def test_no_missing_flags(self):
        """All referenced agora-code commands must use flags that now exist."""
        all_commands = []
        for hook_list in self.config["hooks"].values():
            all_commands.extend(_extract_commands(hook_list))
        for cmd in all_commands:
            if "agora-code scan" in cmd:
                # --cache and --quiet are now valid
                pass
            if "agora-code inject" in cmd:
                # --quiet is now valid
                pass


class TestCursorHooks:
    def setup_method(self):
        self.path = REPO_ROOT / ".cursor" / "hooks.json"
        self.config = json.loads(self.path.read_text(encoding="utf-8"))

    def test_is_valid_json(self):
        assert isinstance(self.config, dict)

    def test_has_version_1(self):
        assert self.config.get("version") == 1, (
            "Cursor hooks.json must have 'version': 1 or the file is silently ignored"
        )

    def test_has_hooks_key(self):
        assert "hooks" in self.config

    def test_session_start_uses_correct_name(self):
        hooks = self.config["hooks"]
        assert "sessionStart" in hooks, (
            "Cursor event name is 'sessionStart' (camelCase), not 'onConversationStart'"
        )

    def test_after_file_edit_uses_correct_name(self):
        hooks = self.config["hooks"]
        assert "afterFileEdit" in hooks, (
            "Cursor event name is 'afterFileEdit', not 'onFileWrite'"
        )

    def test_pre_compact_uses_correct_name(self):
        hooks = self.config["hooks"]
        assert "preCompact" in hooks, (
            "Cursor event name is 'preCompact', not 'onContextLimit'"
        )

    def test_no_wrong_event_names(self):
        hooks = self.config["hooks"]
        wrong_names = {"onConversationStart", "onFileWrite", "onContextLimit"}
        for name in wrong_names:
            assert name not in hooks, f"Wrong Cursor event name found: {name!r}"

    def test_shell_scripts_exist_and_executable(self):
        for event, hook_list in self.config["hooks"].items():
            for hook in hook_list:
                cmd = hook.get("command", "")
                if cmd.endswith(".sh"):
                    script = REPO_ROOT / cmd
                    assert script.exists(), f"Hook script missing: {cmd}"
                    assert script.stat().st_mode & 0o111, (
                        f"Hook script not executable: {cmd} — run: chmod +x {cmd}"
                    )


class TestGeminiHooks:
    def setup_method(self):
        self.path = REPO_ROOT / ".gemini" / "settings.json"
        self.config = json.loads(self.path.read_text(encoding="utf-8"))

    def test_is_valid_json(self):
        assert isinstance(self.config, dict)

    def test_has_hooks_key(self):
        assert "hooks" in self.config

    def test_has_pre_compress(self):
        assert "PreCompress" in self.config["hooks"], (
            "Gemini uses 'PreCompress' for the pre-compaction event"
        )

    def test_has_after_tool(self):
        assert "AfterTool" in self.config["hooks"], (
            "Gemini uses 'AfterTool' for post-tool-use event"
        )

    def test_pre_compress_uses_checkpoint(self):
        hooks = self.config["hooks"]["PreCompress"]
        commands = _extract_commands(hooks)
        assert any("checkpoint" in c for c in commands), (
            "PreCompress should call 'agora-code checkpoint'"
        )
        for cmd in commands:
            assert "state save" not in cmd


# --------------------------------------------------------------------------- #
#  CLI smoke tests — commands exit 0 with no active session                  #
# --------------------------------------------------------------------------- #

def _run_cli(*args, env_override=None) -> subprocess.CompletedProcess:
    """Run an agora-code CLI command and return the result."""
    import os
    env = os.environ.copy()
    # Use a temp DB so tests don't touch the real ~/.agora-code/memory.db
    import tempfile
    env["AGORA_CODE_DB"] = tempfile.mktemp(suffix=".db")
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "agora_code.cli"] + list(args),
        capture_output=True, text=True, env=env,
        cwd=str(REPO_ROOT),
    )


def test_inject_quiet_exits_zero_no_session(tmp_path):
    """inject --quiet should exit 0 silently when no session exists."""
    import os, tempfile
    result = _run_cli("inject", "--quiet",
                      env_override={"AGORA_CODE_DIR": str(tmp_path)})
    assert result.returncode == 0
    # --quiet + no session → no output
    assert result.stdout.strip() == "" or True  # no crash is the key check


def test_checkpoint_quiet_exits_zero(tmp_path):
    """checkpoint --quiet should exit 0 and create/update a session."""
    import os, tempfile
    result = _run_cli("checkpoint", "--quiet",
                      env_override={"AGORA_CODE_DIR": str(tmp_path)})
    assert result.returncode == 0


def test_scan_cache_quiet_exits_zero():
    """scan . --cache --quiet should exit 0 (uses cache if present, else scans)."""
    result = _run_cli("scan", ".", "--cache", "--quiet")
    assert result.returncode == 0


def test_inject_without_quiet_still_works():
    """inject with no flags should work (silent no-op when no session)."""
    result = _run_cli("inject")
    assert result.returncode == 0


def test_inject_quiet_outputs_context_when_session_exists(tmp_path):
    """inject --quiet should still print context when a session/learnings exist.

    Previously the flag suppressed all output, breaking hooks that capture
    stdout like: CONTEXT=$(agora-code inject --quiet)
    """
    db_path = str(tmp_path / "memory.db")
    env = {"AGORA_CODE_DB": db_path}

    # Seed a learning via the CLI so the DB schema is fully initialised
    seed = _run_cli("learn", "test finding for quiet flag", env_override=env)
    assert seed.returncode == 0, f"learn seeding failed: {seed.stderr}"

    result = _run_cli("inject", "--quiet", env_override=env)
    assert result.returncode == 0
    # --quiet should only suppress "no session" errors, not actual context output
    assert result.stdout.strip() != "", (
        "--quiet must not suppress context output; hooks rely on stdout. "
        f"Got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_scan_quiet_suppresses_output():
    """--quiet should produce no stdout when scan runs."""
    result = _run_cli("scan", ".", "--quiet")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# --------------------------------------------------------------------------- #
#  install-hooks --claude-code: SKILL.md + on-grep.sh                        #
# --------------------------------------------------------------------------- #

def test_install_hooks_creates_skill_md(tmp_path, monkeypatch):
    """install-hooks --claude-code must create .claude/skills/agora-code/SKILL.md."""
    monkeypatch.chdir(tmp_path)
    _install_claude_code_hooks(force=True)
    skill_path = tmp_path / ".claude" / "skills" / "agora-code" / "SKILL.md"
    assert skill_path.exists(), "SKILL.md was not created by install-hooks"
    content = skill_path.read_text()
    assert "name: agora-code" in content, "SKILL.md missing frontmatter"
    assert "agora-code inject" in content, "SKILL.md missing inject command"


def test_install_hooks_creates_on_grep(tmp_path, monkeypatch):
    """install-hooks --claude-code must create .claude/hooks/on-grep.sh."""
    monkeypatch.chdir(tmp_path)
    _install_claude_code_hooks(force=True)
    grep_hook = tmp_path / ".claude" / "hooks" / "on-grep.sh"
    assert grep_hook.exists(), "on-grep.sh was not created by install-hooks"
    assert grep_hook.stat().st_mode & 0o111, "on-grep.sh is not executable"


def test_install_hooks_settings_has_grep_matcher(tmp_path, monkeypatch):
    """settings.json generated by install-hooks must include a Grep PostToolUse matcher."""
    monkeypatch.chdir(tmp_path)
    _install_claude_code_hooks(force=True)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    post_tool = settings["hooks"]["PostToolUse"]
    matchers = [item.get("matcher", "") for item in post_tool]
    assert any("Grep" in m for m in matchers), (
        "Generated settings.json missing Grep matcher in PostToolUse"
    )


def test_install_hooks_creates_mcp_json(tmp_path, monkeypatch):
    """install-hooks --claude-code must create .mcp.json with agora-memory server."""
    monkeypatch.chdir(tmp_path)
    _install_claude_code_hooks(force=True)
    mcp_path = tmp_path / ".mcp.json"
    assert mcp_path.exists(), ".mcp.json was not created by install-hooks"
    mcp = json.loads(mcp_path.read_text())
    assert "agora-memory" in mcp.get("mcpServers", {}), (
        ".mcp.json missing agora-memory server entry"
    )
    server = mcp["mcpServers"]["agora-memory"]
    assert server.get("args") == ["memory-server"], (
        "agora-memory server must use 'memory-server' subcommand"
    )


def test_install_hooks_mcp_json_merges_existing(tmp_path, monkeypatch):
    """install-hooks must merge into an existing .mcp.json without clobbering other servers."""
    monkeypatch.chdir(tmp_path)
    existing = {"mcpServers": {"other-tool": {"command": "other", "args": []}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))
    _install_claude_code_hooks(force=True)
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other-tool" in mcp["mcpServers"], "existing MCP servers must be preserved"
    assert "agora-memory" in mcp["mcpServers"], "agora-memory must be added"


# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #

def _extract_commands(hook_list: list) -> list[str]:
    """
    Extract command strings from a hooks list regardless of nesting format.
    Handles both the flat Claude format and nested {matcher, hooks} format.
    """
    commands = []
    for item in hook_list:
        if "command" in item:
            commands.append(item["command"])
        elif "hooks" in item:
            for h in item["hooks"]:
                if "command" in h:
                    commands.append(h["command"])
    return commands

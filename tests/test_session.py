"""
test_session.py — Session lifecycle tests for agora_code/session.py.

All tests use tmp_path / monkeypatch so nothing writes to the real
~/.agora-code or .agora-code directories.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agora_code.session import (
    _extract_ticket,
    _branch_to_goal,
    _get_git_branch,
    _get_commit_sha,
    load_session,
    load_session_if_recent,
    new_session,
    save_session,
    update_session,
    get_session_path,
)


# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #

def _patch_session_dir(monkeypatch, tmp_path: Path) -> Path:
    """
    Redirect session.py to read/write inside tmp_path.
    Creates .agora-code/ in tmp_path and chdir's there so _find_project_root
    anchors to tmp_path immediately without monkeypatching the function itself.
    """
    import agora_code.session as sess_mod
    (tmp_path / ".agora-code").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    # Redirect global fallback so ~/.agora-code/session.json is not used
    monkeypatch.setattr(sess_mod, "_GLOBAL_DIR", tmp_path / ".agora-code-global")
    return tmp_path


# --------------------------------------------------------------------------- #
#  new_session                                                                #
# --------------------------------------------------------------------------- #

def test_new_session_has_required_keys():
    s = new_session(goal="Test the API")
    assert "session_id" in s
    assert "started_at" in s
    assert "last_active" in s
    assert "status" in s
    assert s["status"] == "in_progress"


def test_new_session_goal_stored():
    s = new_session(goal="Fix auth bug")
    assert s["goal"] == "Fix auth bug"


def test_new_session_no_goal_does_not_crash():
    s = new_session()
    assert isinstance(s["session_id"], str)
    assert len(s["session_id"]) > 0


def test_new_session_session_id_is_string():
    s = new_session(goal="hello")
    assert isinstance(s["session_id"], str)


def test_new_session_lists_are_empty():
    s = new_session()
    assert s["discoveries"] == []
    assert s["next_steps"] == []
    assert s["blockers"] == []
    assert s["files_changed"] == []


def test_new_session_timestamps_are_iso():
    s = new_session()
    # Should parse without raising
    datetime.fromisoformat(s["started_at"])
    datetime.fromisoformat(s["last_active"])


# --------------------------------------------------------------------------- #
#  save_session + load_session round-trip                                     #
# --------------------------------------------------------------------------- #

def test_save_and_load_round_trip(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = new_session(goal="Round-trip test")
    save_session(s)
    loaded = load_session()
    assert loaded is not None
    assert loaded["goal"] == "Round-trip test"


def test_save_updates_last_active(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = new_session(goal="Timing test")
    original_last_active = s["last_active"]
    import time; time.sleep(0.01)
    save_session(s)
    loaded = load_session()
    # last_active should be updated to now (>= original)
    assert loaded["last_active"] >= original_last_active


def test_load_session_returns_none_when_missing(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    result = load_session()
    assert result is None


def test_load_session_returns_none_on_corrupt_file(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    session_dir = tmp_path / ".agora-code"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.json").write_text("not valid json", encoding="utf-8")
    result = load_session()
    assert result is None


# --------------------------------------------------------------------------- #
#  update_session                                                             #
# --------------------------------------------------------------------------- #

def test_update_session_creates_if_missing(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = update_session({"goal": "New goal"})
    assert s["goal"] == "New goal"
    assert s["status"] == "in_progress"


def test_update_session_merges_fields(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    update_session({"goal": "Original goal"})
    s2 = update_session({"hypothesis": "My hypothesis"})
    assert s2["goal"] == "Original goal"
    assert s2["hypothesis"] == "My hypothesis"


def test_update_session_overwrites_goal(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    update_session({"goal": "Old goal"})
    s = update_session({"goal": "New goal"})
    assert s["goal"] == "New goal"


def test_update_session_stores_next_steps(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = update_session({"next_steps": ["step 1", "step 2"]})
    assert "step 1" in s["next_steps"]
    assert "step 2" in s["next_steps"]


def test_update_session_stores_files_changed(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = update_session({"files_changed": [{"file": "auth.py", "what": "added retry"}]})
    assert any(f["file"] == "auth.py" for f in s["files_changed"])


def test_update_session_persists_to_disk(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    update_session({"goal": "Persist me"})
    loaded = load_session()
    assert loaded["goal"] == "Persist me"


# --------------------------------------------------------------------------- #
#  load_session_if_recent                                                     #
# --------------------------------------------------------------------------- #

def test_load_if_recent_returns_session_when_fresh(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = new_session(goal="Fresh session")
    save_session(s)
    result = load_session_if_recent(max_age_hours=24)
    assert result is not None
    assert result["goal"] == "Fresh session"


def test_load_if_recent_returns_none_when_stale(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    s = new_session(goal="Old session")
    # Backdate last_active by 50 hours — write directly to bypass
    # save_session's automatic last_active refresh
    stale_time = datetime.now(timezone.utc) - timedelta(hours=50)
    s["last_active"] = stale_time.isoformat()
    session_file = tmp_path / ".agora-code" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(json.dumps(s), encoding="utf-8")

    result = load_session_if_recent(max_age_hours=24)
    assert result is None


def test_load_if_recent_returns_none_when_no_session(monkeypatch, tmp_path):
    _patch_session_dir(monkeypatch, tmp_path)
    result = load_session_if_recent()
    assert result is None


# --------------------------------------------------------------------------- #
#  _extract_ticket                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("branch,expected", [
    ("JIRA-423-fix-login",       "JIRA-423"),
    ("feature/JIRA-423-login",   "JIRA-423"),
    ("fix/gh-456-null-ptr",      "GH-456"),
    ("GH-78-perf",               "GH-78"),
    ("feat/auth-service",        None),
    ("main",                     None),
    (None,                       None),
    ("",                         None),
])
def test_extract_ticket(branch, expected):
    result = _extract_ticket(branch)
    assert result == expected


# --------------------------------------------------------------------------- #
#  _branch_to_goal                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("branch,expected_contains", [
    ("feat/auth-service",    "feat/auth-service"),
    ("JIRA-423-fix-login",   "JIRA-423"),
    ("main",                 None),   # main → returns None
    (None,                   None),
])
def test_branch_to_goal(branch, expected_contains):
    result = _branch_to_goal(branch)
    if expected_contains is None:
        assert result is None
    else:
        assert expected_contains in result


# --------------------------------------------------------------------------- #
#  Git helpers — graceful fallback outside a git repo                        #
# --------------------------------------------------------------------------- #

def test_get_git_branch_returns_str_or_none():
    result = _get_git_branch()
    assert result is None or isinstance(result, str)


def test_get_commit_sha_returns_str_or_none():
    result = _get_commit_sha()
    assert result is None or isinstance(result, str)


def test_get_git_branch_in_actual_repo():
    """We ARE in a git repo (agora-code itself), so branch should not be None."""
    result = _get_git_branch()
    assert result is not None
    assert len(result) > 0


# --------------------------------------------------------------------------- #
#  inject format — _build_recalled_context                                    #
# --------------------------------------------------------------------------- #

def _make_store_with_learning(tmp_path, finding, evidence, tags, commit_sha="abc1234", files=None):
    """Create an in-memory VectorStore with one learning row."""
    import json
    from agora_code.vector_store import VectorStore
    db_path = tmp_path / "memory.db"
    vs = VectorStore(str(db_path))
    vs.store_learning(
        finding=finding,
        evidence=evidence,
        confidence="confirmed",
        tags=tags,
        type="finding",
        branch="main",
        files=files or ["agora_code/vector_store.py"],
        project_id="test-project",
        session_id=None,
        commit_sha=commit_sha,
    )
    return vs


def test_inject_learning_shows_evidence_not_finding(tmp_path, monkeypatch):
    """inject LEARNINGS line should show evidence (commit message), not the mechanical diff stat."""
    from agora_code.session import _build_recalled_context
    import agora_code.session as sess_mod

    vs = _make_store_with_learning(
        tmp_path,
        finding="modified get_file_history() (+30/-14 lines) #kept",
        evidence="commit abc1234: fix: strict project_id scoping",
        tags=["commit", "change-note"],
        commit_sha="abc1234",
    )

    monkeypatch.setattr(sess_mod, "_GLOBAL_DIR", tmp_path / ".agora-code-global")
    monkeypatch.setattr("agora_code.session.get_store", lambda: vs)
    monkeypatch.setattr("agora_code.session._get_project_id", lambda: "test-project")

    ctx = _build_recalled_context("test-project")
    assert ctx is not None
    assert "fix: strict project_id scoping" in ctx
    # mechanical diff stat should NOT be the primary line
    assert "modified get_file_history() (+30/-14 lines)" not in ctx


def test_inject_learning_single_line_per_entry(tmp_path, monkeypatch):
    """Each learning should produce exactly one line in the LEARNINGS block."""
    from agora_code.session import _build_recalled_context
    import agora_code.session as sess_mod

    vs = _make_store_with_learning(
        tmp_path,
        finding="modified foo() (+5/-2 lines) #kept",
        evidence="commit abc1234: feat: add foo",
        tags=["commit", "change-note"],
        commit_sha="abc1234",
    )

    monkeypatch.setattr(sess_mod, "_GLOBAL_DIR", tmp_path / ".agora-code-global")
    monkeypatch.setattr("agora_code.session.get_store", lambda: vs)
    monkeypatch.setattr("agora_code.session._get_project_id", lambda: "test-project")

    ctx = _build_recalled_context("test-project")
    assert ctx is not None
    lines = [l for l in ctx.splitlines() if "feat: add foo" in l]
    assert len(lines) == 1


# --------------------------------------------------------------------------- #
#  LAST CHECKPOINT — built from the latest session row                        #
# --------------------------------------------------------------------------- #

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    import agora_code.vector_store as vs_mod
    _patch_session_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("AGORA_CODE_DB", str(tmp_path / "memory.db"))
    monkeypatch.setattr(vs_mod, "_store", None)
    return vs_mod.get_store()


@pytest.fixture
def git_repo(isolated_store, tmp_path):
    """Empty git repo at tmp_path (cwd). Returns a function that makes a commit and returns its sha."""
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q")
    git("symbolic-ref", "HEAD", "refs/heads/main")

    def commit(msg="c"):
        git("commit", "-q", "--allow-empty", "-m", msg)
        return git("rev-parse", "HEAD")

    commit.git = git
    return commit


def _ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def _checkpoint(*args):
    from click.testing import CliRunner
    from agora_code.cli import main
    return CliRunner().invoke(main, ["checkpoint", *args, "--quiet"], catch_exceptions=False)


def test_empty_update_does_not_mark_checkpoint(isolated_store):
    """Hook calls like `checkpoint --quiet` must not refresh the checkpoint timestamp."""
    assert update_session({})["checkpointed_at"] is None
    stamped = update_session({"goal": "Ship it"})["checkpointed_at"]
    assert update_session({})["checkpointed_at"] == stamped


def test_project_checkpoint_does_not_inherit_global_session(isolated_store):
    import agora_code.session as sess_mod
    global_dir = sess_mod._GLOBAL_DIR
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "session.json").write_text(json.dumps({
        "session_id": "global-old", "goal": "unrelated scratch work",
        "blockers": ["paste the error please"],
    }))
    s = update_session({"goal": "Project task"})
    assert s["session_id"] != "global-old"
    assert s["blockers"] == []
    assert json.loads((global_dir / "session.json").read_text())["goal"] == "unrelated scratch work"


def test_mcp_context_autofill_is_not_a_checkpoint(isolated_store):
    import asyncio
    from agora_code.memory_server import _handle_get_session_context
    stamped = update_session({"goal": "Ship it"})["checkpointed_at"]
    asyncio.run(_handle_get_session_context({}))
    session = load_session()
    assert session.get("context"), "handler should have auto-filled context"
    assert session["checkpointed_at"] == stamped


def test_session_ids_are_unique():
    assert new_session(goal="same goal")["session_id"] != new_session(goal="same goal")["session_id"]


def test_inject_shows_last_checkpoint_from_session(isolated_store):
    from agora_code.session import _build_recalled_context
    update_session({
        "goal": "Refactor auth",
        "next_steps": ["write tests"],
        "blockers": ["waiting on review"],
    })
    ctx = _build_recalled_context()
    assert ctx is not None
    assert "LAST CHECKPOINT (just now)" in ctx
    assert "Refactor auth" in ctx
    assert "write tests" in ctx
    assert "waiting on review" in ctx


def test_inject_ignores_legacy_checkpoint_learnings(isolated_store):
    from agora_code.session import _build_recalled_context, _get_project_id
    isolated_store.store_learning(
        "CHECKPOINT | goal: old",
        evidence=json.dumps({"goal": "Old legacy goal"}),
        tags=["checkpoint", "structured"],
        project_id=_get_project_id(),
    )
    assert "Old legacy goal" not in (_build_recalled_context() or "")


def test_inject_shows_old_completed_checkpoint_and_scopes_by_project(isolated_store):
    """No age cutoff, completed sessions count, and other projects never leak in."""
    from agora_code.session import _build_recalled_context
    old = {
        "session_id": "old-a", "status": "complete", "goal": "A: billing export",
        "summary": "shipped", "checkpointed_at": _ago(days=90),
    }
    isolated_store.save_session(old, project_id="proj-a")
    isolated_store._conn_().execute(
        "UPDATE sessions SET last_active = ? WHERE session_id = 'old-a'", (_ago(days=90),))
    isolated_store._conn_().commit()
    isolated_store.save_session(
        {"session_id": "new-b", "goal": "B: parser work", "checkpointed_at": _ago(minutes=1)},
        project_id="proj-b",
    )
    ctx = _build_recalled_context("proj-a") or ""
    assert "LAST CHECKPOINT (3 months ago)" in ctx
    assert "A: billing export" in ctx and "completed:  shipped" in ctx
    assert "B: parser work" not in ctx


def test_inject_skips_checkpoint_without_project_id(isolated_store, monkeypatch):
    import agora_code.session as sess_mod
    isolated_store.save_session(
        {"session_id": "x", "goal": "someone else's goal", "checkpointed_at": _ago(minutes=1)},
        project_id="proj-x",
    )
    monkeypatch.setattr(sess_mod, "_get_project_id", lambda: "")
    assert "someone else's goal" not in (sess_mod._build_recalled_context() or "")


@pytest.mark.parametrize("since, current_branch, expected", [
    (0, "feat/x", "LAST CHECKPOINT (2 months ago)"),
    (1, "feat/x", "LAST CHECKPOINT (2 months ago, 1 commit since — may be outdated)"),
    (4, "feat/x", "LAST CHECKPOINT (2 months ago, 4 commits since — may be outdated)"),
    (None, "main", "LAST CHECKPOINT (2 months ago, saved on branch feat/x)"),
    (None, "feat/x", "LAST CHECKPOINT (2 months ago, its commit is no longer in this branch's history)"),
])
def test_checkpoint_label(monkeypatch, since, current_branch, expected):
    import agora_code.session as sess_mod
    from agora_code.session import _format_last_checkpoint
    monkeypatch.setattr(sess_mod, "_commits_since", lambda sha: since)
    monkeypatch.setattr(sess_mod, "_get_git_branch", lambda: current_branch)
    block = _format_last_checkpoint({
        "goal": "Refactor auth",
        "checkpointed_at": _ago(days=61),
        "checkpoint_commit_sha": "abc1234",
        "checkpoint_branch": "feat/x",
        "branch": current_branch,
    })
    assert block.splitlines()[0] == expected


def test_legacy_checkpoint_does_not_trust_hook_refreshed_fields(monkeypatch):
    """Without checkpointed_at, last_active/branch/commit_sha are hook-refreshed and meaningless."""
    import agora_code.session as sess_mod
    from agora_code.session import _format_last_checkpoint
    monkeypatch.setattr(sess_mod, "_commits_since", lambda sha: 0)
    block = _format_last_checkpoint({
        "goal": "Old goal", "last_active": _ago(seconds=5),
        "branch": "main", "commit_sha": "abc1234",
    })
    assert block.splitlines()[0] == "LAST CHECKPOINT (age unknown — saved by an older agora-code)"
    assert "branch:" not in block


def test_checkpoint_without_content_renders_nothing():
    from agora_code.session import _format_last_checkpoint
    assert _format_last_checkpoint({"goal": "", "checkpointed_at": _ago(minutes=1)}) is None


@pytest.mark.parametrize("session, stale", [
    ({}, True),
    ({"goal": "legacy, never checkpointed"}, True),
    ({"goal": "hook-created", "checkpointed_at": None}, True),
    ({"checkpointed_at": "2026-01-01T00:00:00+00:00", "status": "complete"}, True),
    ({"checkpointed_at": "2026-01-01T00:00:00+00:00"}, False),
    ({"checkpointed_at": "2026-01-01T00:00:00+00:00", "checkpoint_commit_sha": "abc1234"}, False),
])
def test_checkpoint_is_stale(session, stale):
    from agora_code.session import checkpoint_is_stale
    assert checkpoint_is_stale(session) is stale


def test_never_checkpointed_session_renders_nothing():
    from agora_code.session import _format_last_checkpoint
    assert _format_last_checkpoint(new_session(goal="Working on feat/login")) is None


def test_legacy_session_is_replaced_not_merged(isolated_store, tmp_path):
    """Pre-upgrade session files may be copies of another project's session."""
    (tmp_path / ".agora-code" / "session.json").write_text(json.dumps({
        "session_id": "2026-03-10-main", "goal": "unrelated March goal",
        "blockers": ["paste the error please"],
    }))
    s = update_session({"goal": "Real task"})
    assert s["session_id"] != "2026-03-10-main"
    assert s["blockers"] == []


def test_complete_without_own_session_writes_valid_session(isolated_store):
    import agora_code.session as sess_mod
    from agora_code.session import archive_session
    sess_mod._GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    (sess_mod._GLOBAL_DIR / "session.json").write_text(json.dumps({"session_id": "global-1"}))
    done = archive_session(summary="done")
    assert done["session_id"] and done["session_id"] != "global-1"
    assert update_session({"goal": "next thing"})["goal"] == "next thing"


def test_if_stale_without_content_writes_nothing(isolated_store, tmp_path):
    _checkpoint("--if-stale")
    assert not (tmp_path / ".agora-code" / "session.json").exists()


@needs_git
def test_commits_since_real_git(git_repo):
    from agora_code.session import _commits_since
    first = git_repo("one")
    git_repo("two")
    head = git_repo("three")
    assert _commits_since(first) == 2
    assert _commits_since(first[:12]) == 2
    assert _commits_since(head) == 0
    assert _commits_since(None) == 3
    assert _commits_since("HEAD~1") is None
    assert _commits_since("--output=/tmp/x") is None
    git_repo.git("checkout", "-q", "-b", "other", first)
    git_repo("diverged")
    assert _commits_since(head) is None


@needs_git
def test_commits_mid_task_keep_working_state(git_repo):
    """A follow-up prompt after a commit must not wipe the task's next steps and blockers."""
    from agora_code.session import _build_recalled_context
    git_repo("init")
    _checkpoint("--goal", "Refactor auth module", "--next", "write token tests",
                "--blocker", "waiting on API key")
    first_id = load_session()["session_id"]

    git_repo("auth refactor")
    _checkpoint("--goal", "now add retry tests for the auth module please", "--if-stale")
    session = load_session()
    assert session["session_id"] == first_id
    assert session["goal"] == "Refactor auth module"
    assert session["next_steps"] == ["write token tests"]

    ctx = _build_recalled_context() or ""
    assert "1 commit since — may be outdated" in ctx
    assert "write token tests" in ctx and "waiting on API key" in ctx


@needs_git
def test_restored_session_survives_next_prompt(git_repo):
    from click.testing import CliRunner
    from agora_code.cli import main
    from agora_code.session import _build_recalled_context, archive_session
    git_repo("init")
    _checkpoint("--goal", "Refactor auth module", "--next", "write token tests")
    auth_id = load_session()["session_id"]
    git_repo("auth work")
    archive_session(summary="paused auth")
    _checkpoint("--goal", "Build the billing export", "--if-stale")
    assert load_session()["session_id"] != auth_id

    CliRunner().invoke(main, ["restore", auth_id], catch_exceptions=False)
    _checkpoint("--goal", "continue with the restored auth work", "--if-stale")
    session = load_session()
    assert session["session_id"] == auth_id
    assert session["next_steps"] == ["write token tests"]
    assert "Refactor auth module" in (_build_recalled_context() or "")


def test_restored_legacy_session_is_kept(isolated_store):
    """Sessions saved before checkpoint tracking must stay active once explicitly restored."""
    from click.testing import CliRunner
    from agora_code.cli import main
    from agora_code.session import _get_project_id
    isolated_store.save_session(
        {"session_id": "2026-03-10-main", "status": "complete", "goal": "Old auth work",
         "next_steps": ["write token tests"]},
        project_id=_get_project_id(),
    )
    CliRunner().invoke(main, ["restore", "2026-03-10-main"], catch_exceptions=False)
    _checkpoint("--goal", "continue the old auth work please", "--if-stale")
    _checkpoint("--next", "also cover refresh expiry")
    session = load_session()
    assert session["session_id"] == "2026-03-10-main"
    assert session["goal"] == "Old auth work"


def test_list_sessions_prints_full_ids(isolated_store):
    from click.testing import CliRunner
    from agora_code.cli import main
    _checkpoint("--goal", "a fairly long goal that makes the generated session id long")
    session_id = load_session()["session_id"]
    assert len(session_id) > 44
    out = CliRunner().invoke(main, ["list-sessions"], catch_exceptions=False).output
    assert session_id in out


@needs_git
def test_contentless_newer_session_does_not_hide_checkpoint(git_repo, tmp_path):
    """A hook-created session (no content, goal from branch name) must not mask the real checkpoint."""
    from agora_code.session import _build_recalled_context
    git_repo("init")
    _checkpoint("--goal", "Refactor auth module")
    git_repo.git("checkout", "-q", "-b", "feature/login-retry")
    (tmp_path / ".agora-code" / "session.json").unlink()
    update_session({})
    ctx = _build_recalled_context() or ""
    assert "Refactor auth module" in ctx
    assert "older agora-code" not in ctx


@needs_git
def test_completed_session_is_replaced_by_next_task(git_repo):
    from agora_code.session import _build_recalled_context, archive_session, checkpoint_is_stale
    git_repo("init")
    _checkpoint("--goal", "Refactor auth module")
    head = git_repo("finish auth")
    done = archive_session(summary="Refactored auth")
    assert head.startswith(done["checkpoint_commit_sha"]), "complete must stamp the commit it finished at"
    assert checkpoint_is_stale() is True

    _checkpoint("--goal", "Investigate the flaky websocket test", "--if-stale")
    session = load_session()
    assert session["status"] == "in_progress"
    ctx = _build_recalled_context() or ""
    assert "Investigate the flaky websocket test" in ctx
    assert "completed:" not in ctx

"""
test_session.py — Session lifecycle tests for agora_code/session.py.

All tests use tmp_path / monkeypatch so nothing writes to the real
~/.agora-code or .agora-code directories.
"""
from __future__ import annotations

import json
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

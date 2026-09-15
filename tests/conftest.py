"""
conftest.py — Shared fixtures for agora-code test suite.
"""
from __future__ import annotations

import site
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_agora_state(tmp_path_factory, monkeypatch):
    """Keep every test (and CLI subprocess) away from the real ~/.agora-code DB,
    the global session.json and ~/.claude."""
    import agora_code.session as sess_mod
    import agora_code.vector_store as vs_mod

    home = tmp_path_factory.mktemp("home")
    # Subprocesses find user-site packages (e.g. click) via HOME unless pinned
    monkeypatch.setenv("PYTHONUSERBASE", site.getuserbase())
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGORA_CODE_DB", str(home / "memory.db"))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(sess_mod, "_GLOBAL_DIR", home / ".agora-code")
    monkeypatch.setattr(vs_mod, "_store", None)

"""
conftest.py — Shared fixtures for agora-code test suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator

import pytest

from agora_code.models import Param, Route, RouteCatalog

# --------------------------------------------------------------------------- #
#  Paths                                                                       #
# --------------------------------------------------------------------------- #

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
#  Model fixtures                                                              #
# --------------------------------------------------------------------------- #

@pytest.fixture
def sample_route() -> Route:
    return Route(
        method="GET",
        path="/users/{user_id}",
        description="Fetch a user by ID.",
        params=[
            Param(name="user_id", type="int", location="path", required=True),
            Param(name="include_details", type="bool", location="query",
                  required=False, default=False),
        ],
    )


@pytest.fixture
def sample_catalog(sample_route) -> RouteCatalog:
    return RouteCatalog(
        source="test",
        extractor="test",
        routes=[
            sample_route,
            Route(method="POST", path="/users", description="Create a user.",
                  params=[
                      Param(name="name", type="str", location="body", required=True),
                      Param(name="email", type="str", location="body", required=True),
                  ]),
        ],
    )


# --------------------------------------------------------------------------- #
#  Code fixtures                                                               #
# --------------------------------------------------------------------------- #

@pytest.fixture
def fastapi_code() -> str:
    return (FIXTURES_DIR / "sample_fastapi.py").read_text()


@pytest.fixture
def flask_code() -> str:
    return (FIXTURES_DIR / "sample_flask.py").read_text()


@pytest.fixture
def openapi_spec() -> dict:
    return json.loads((FIXTURES_DIR / "sample_openapi.json").read_text())


# --------------------------------------------------------------------------- #
#  Memory fixture — in-memory SQLite, auto-cleaned                            #
# --------------------------------------------------------------------------- #

@pytest.fixture
async def memory_store():
    """MemoryStore backed by a temp-file SQLite. Skipped if agora-mem missing."""
    import tempfile, os
    pytest.importorskip("agora_mem")
    from agora_mem import MemoryStore

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_memory.db")
        store = MemoryStore(storage="sqlite", db_path=db_path)
        yield store

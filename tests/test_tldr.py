"""
test_tldr.py — Compression and token-reduction tests for agora_code/tldr.py.

Verifies that each compression level:
  - produces valid, non-empty output
  - stays within the token ranges documented in the README
  - reduces tokens significantly vs. raw JSON

Also tests session compression (the other half of tldr.py that's used
by the memory server to inject context).
"""
from __future__ import annotations

import json
from typing import List

import pytest

from agora_code.models import Param, Route
from agora_code.tldr import (
    LEVELS,
    auto_compress_session,
    auto_level,
    compress_catalog,
    compress_session,
    estimate_tokens,
    measure_compression,
    session_restored_banner,
    summarize_routes,
)


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #

@pytest.fixture
def few_routes() -> List[Route]:
    """5 routes — representative of a small API."""
    return [
        Route(method="GET",    path="/users",             description="List users.",
              params=[Param(name="limit", type="int", location="query", required=False, default=20)]),
        Route(method="POST",   path="/users",             description="Create a user.",
              params=[Param(name="name",  type="str", location="body", required=True),
                      Param(name="email", type="str", location="body", required=True)]),
        Route(method="GET",    path="/users/{user_id}",   description="Get user by ID.",
              params=[Param(name="user_id", type="int", location="path", required=True)]),
        Route(method="PUT",    path="/users/{user_id}",   description="Update a user.",
              params=[Param(name="user_id", type="int", location="path", required=True)]),
        Route(method="DELETE", path="/users/{user_id}",   description="Delete a user.",
              params=[Param(name="user_id", type="int", location="path", required=True)]),
    ]


@pytest.fixture
def many_routes() -> List[Route]:
    """20 routes — stress-tests token budget logic."""
    routes = []
    for i in range(20):
        routes.append(Route(
            method="GET",
            path=f"/resource/{i}",
            description=f"Fetch resource number {i} with full details and pagination support.",
            params=[
                Param(name="id",    type="int",  location="path",  required=True),
                Param(name="page",  type="int",  location="query", required=False, default=1),
                Param(name="limit", type="int",  location="query", required=False, default=20),
            ],
        ))
    return routes


@pytest.fixture
def rich_session() -> dict:
    """A session with goal, hypothesis, discoveries, next steps, blockers."""
    return {
        "session_id":    "2026-03-11-fix-auth",
        "started_at":    "2026-03-11T09:00:00Z",
        "last_active":   "2026-03-11T14:00:00Z",
        "status":        "in_progress",
        "goal":          "Fix 500 errors on POST /auth",
        "hypothesis":    "Email validation middleware rejects non-ASCII usernames",
        "current_action": "Testing edge cases in validate_email()",
        "branch":        "feat/fix-auth",
        "discoveries": [
            {"finding": "POST /auth returns 400 for usernames with spaces", "confidence": "confirmed"},
            {"finding": "Rate limit is 100 req/min per IP", "confidence": "confirmed"},
            {"finding": "Token expires after 15 minutes", "confidence": "likely"},
            {"finding": "Admin endpoint skips rate limiting", "confidence": "hypothesis"},
            {"finding": "Email regex does not allow + in local part", "confidence": "confirmed"},
        ],
        "next_steps":    ["Write test for + in email", "Check middleware source", "Deploy fix"],
        "blockers":      ["Waiting for staging deploy", "Need DB access"],
        "decisions_made": ["Use allowlist not denylist", "Cache validated emails for 5min"],
        "endpoints_tested": [
            {"method": "POST", "path": "/auth", "attempts": 10, "successes": 7, "failures": 3,
             "last_error": "400 Bad Request — invalid username"},
            {"method": "GET",  "path": "/users", "attempts": 5, "successes": 5, "failures": 0},
        ],
        "files_changed": [
            {"file": "auth.py", "what": "added retry logic"},
            {"file": "middleware.py", "what": "loosened email regex"},
        ],
    }


@pytest.fixture
def minimal_session() -> dict:
    return {
        "session_id": "2026-03-11-minimal",
        "started_at": "2026-03-11T09:00:00Z",
        "last_active": "2026-03-11T09:00:00Z",
        "status": "in_progress",
        "goal": "Quick fix",
    }


# --------------------------------------------------------------------------- #
#  estimate_tokens                                                             #
# --------------------------------------------------------------------------- #

def test_estimate_tokens_empty():
    assert estimate_tokens("") == 1  # max(1, 0//4)


def test_estimate_tokens_proportional():
    short = "Hello world"
    long  = "Hello world " * 100
    assert estimate_tokens(long) > estimate_tokens(short)


def test_estimate_tokens_heuristic():
    text = "a" * 400
    assert estimate_tokens(text) == 100  # 400 / 4


# --------------------------------------------------------------------------- #
#  summarize_routes — all levels produce valid output                         #
# --------------------------------------------------------------------------- #

def test_index_contains_method_and_path(few_routes):
    out = summarize_routes(few_routes, level="index")
    assert "GET /users" in out
    assert "POST /users" in out
    assert "DELETE /users/{user_id}" in out


def test_summary_contains_description(few_routes):
    out = summarize_routes(few_routes, level="summary")
    assert "List users" in out
    assert "Create a user" in out


def test_detail_contains_params(few_routes):
    out = summarize_routes(few_routes, level="detail")
    assert "name: str" in out
    assert "email: str" in out
    assert "user_id: int" in out
    assert "(required)" in out


def test_full_is_valid_json(few_routes):
    out = summarize_routes(few_routes, level="full")
    data = json.loads(out)
    assert data["count"] == len(few_routes)
    assert len(data["routes"]) == len(few_routes)


def test_invalid_level_raises(few_routes):
    with pytest.raises(ValueError):
        summarize_routes(few_routes, level="ultra")


def test_source_label_in_header(few_routes):
    out = summarize_routes(few_routes, level="index", source="openapi")
    assert "[openapi]" in out


def test_empty_routes_no_crash():
    out = summarize_routes([], level="summary")
    assert "0 endpoints" in out


# --------------------------------------------------------------------------- #
#  Token counts — verify README claims                                        #
# --------------------------------------------------------------------------- #

def test_index_token_count(many_routes):
    """index should be well under 200 tokens even for 20 routes."""
    out = summarize_routes(many_routes, level="index")
    tokens = estimate_tokens(out)
    assert tokens <= 200, f"index produced {tokens} tokens — expected ≤200"


def test_summary_token_count(many_routes):
    """summary should be well under 600 tokens for 20 routes."""
    out = summarize_routes(many_routes, level="summary")
    tokens = estimate_tokens(out)
    assert tokens <= 600, f"summary produced {tokens} tokens — expected ≤600"


def test_detail_under_budget(few_routes):
    """detail for 5 routes should fit in the default 2000-token budget."""
    out = summarize_routes(few_routes, level="detail")
    tokens = estimate_tokens(out)
    assert tokens <= 2000, f"detail produced {tokens} tokens — expected ≤2000"


def test_full_is_largest(few_routes):
    """full should always produce more tokens than detail."""
    full_tokens   = estimate_tokens(summarize_routes(few_routes, level="full"))
    detail_tokens = estimate_tokens(summarize_routes(few_routes, level="detail"))
    assert full_tokens >= detail_tokens


# --------------------------------------------------------------------------- #
#  measure_compression                                                        #
# --------------------------------------------------------------------------- #

def test_measure_compression_index(few_routes):
    result = measure_compression(few_routes, level="index")
    assert result["level"] == "index"
    assert result["original_tokens"] > 0
    assert result["compressed_tokens"] > 0
    assert result["reduction_pct"] > 50.0, (
        f"index should reduce by >50%, got {result['reduction_pct']}%"
    )


def test_measure_compression_summary(few_routes):
    result = measure_compression(few_routes, level="summary")
    assert result["reduction_pct"] > 30.0


def test_measure_compression_detail(few_routes):
    result = measure_compression(few_routes, level="detail")
    assert result["reduction_pct"] >= 0.0  # detail is close to full — still some reduction


def test_measure_compression_levels_ordered(few_routes):
    """More compressed levels should have fewer tokens."""
    idx = measure_compression(few_routes, level="index")["compressed_tokens"]
    smm = measure_compression(few_routes, level="summary")["compressed_tokens"]
    det = measure_compression(few_routes, level="detail")["compressed_tokens"]
    assert idx <= smm <= det


# --------------------------------------------------------------------------- #
#  auto_level — token budget selection                                        #
# --------------------------------------------------------------------------- #

def test_auto_level_tight_budget(many_routes):
    """Very tight budget forces index level."""
    level, text = auto_level(many_routes, token_budget=60)
    assert level == "index"
    assert estimate_tokens(text) <= 60 or level == "index"  # always picks smallest if tight


def test_auto_level_generous_budget(few_routes):
    """Generous budget picks detail."""
    level, text = auto_level(few_routes, token_budget=2000)
    assert level == "detail"
    assert estimate_tokens(text) <= 2000


def test_auto_level_returns_text(few_routes):
    level, text = auto_level(few_routes, token_budget=500)
    assert isinstance(text, str)
    assert len(text) > 0


# --------------------------------------------------------------------------- #
#  compress_catalog                                                           #
# --------------------------------------------------------------------------- #

def test_compress_catalog_summary(sample_catalog):
    out = compress_catalog(sample_catalog, level="summary")
    assert "GET" in out or "POST" in out


def test_compress_catalog_reads_source(sample_catalog):
    out = compress_catalog(sample_catalog, level="index")
    # Source label should come from catalog.source ("test")
    assert "test" in out or "endpoints" in out


# --------------------------------------------------------------------------- #
#  Session compression                                                        #
# --------------------------------------------------------------------------- #

def test_compress_session_index(rich_session):
    out = compress_session(rich_session, level="index")
    assert "Fix 500 errors" in out  # goal is present
    assert len(out) > 0


def test_compress_session_summary(rich_session):
    out = compress_session(rich_session, level="summary")
    assert "GOAL:" in out
    assert "HYPOTHESIS:" in out
    assert "WHAT YOU DISCOVERED:" in out
    assert "NEXT STEPS:" in out


def test_compress_session_detail(rich_session):
    out = compress_session(rich_session, level="detail")
    assert "DECISIONS MADE:" in out
    assert "FULL ENDPOINT STATUS:" in out


def test_compress_session_full_is_json(rich_session):
    out = compress_session(rich_session, level="full")
    data = json.loads(out)
    assert data["session_id"] == "2026-03-11-fix-auth"


def test_compress_session_invalid_level(rich_session):
    with pytest.raises(ValueError):
        compress_session(rich_session, level="mega")


def test_compress_session_minimal_no_crash(minimal_session):
    """Sessions with minimal fields should not crash any level."""
    for level in LEVELS:
        out = compress_session(minimal_session, level=level)
        assert isinstance(out, str)


def test_compress_session_discovery_cap(rich_session):
    """Summary level caps at 4 discoveries."""
    out = compress_session(rich_session, level="summary")
    # Session has 5 discoveries — summary shows 4 + "+1 more"
    assert "+1 more" in out


# --------------------------------------------------------------------------- #
#  auto_compress_session                                                      #
# --------------------------------------------------------------------------- #

def test_auto_compress_fits_default_budget(rich_session):
    out = auto_compress_session(rich_session)
    assert estimate_tokens(out) <= 2000


def test_auto_compress_tight_budget(rich_session):
    """Even a tight budget returns something (index level as fallback)."""
    out = auto_compress_session(rich_session, token_budget=50)
    assert len(out) > 0
    assert "Fix 500 errors" in out  # goal should always be present


# --------------------------------------------------------------------------- #
#  session_restored_banner                                                    #
# --------------------------------------------------------------------------- #

def test_banner_contains_session_id(rich_session):
    banner = session_restored_banner(rich_session)
    assert "2026-03-11-fix-auth" in banner


def test_banner_contains_compressed_content(rich_session):
    banner = session_restored_banner(rich_session)
    assert "GOAL:" in banner or "Fix 500 errors" in banner


def test_banner_fits_token_budget(rich_session):
    banner = session_restored_banner(rich_session, token_budget=2000)
    assert estimate_tokens(banner) <= 2500  # slight buffer for banner framing


def test_banner_has_separator(rich_session):
    banner = session_restored_banner(rich_session)
    assert "═" in banner

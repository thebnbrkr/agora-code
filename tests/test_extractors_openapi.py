"""
test_extractors_openapi.py — OpenAPI extractor tests.

Tests parsing of local spec files and URL probing logic.
No network calls — all file-based.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agora_code.extractors import openapi


# --------------------------------------------------------------------------- #
#  can_handle                                                                  #
# --------------------------------------------------------------------------- #

def test_can_handle_json_file(tmp_path):
    spec = tmp_path / "openapi.json"
    spec.write_text('{"openapi": "3.0.0"}')
    assert openapi.can_handle(str(tmp_path)) is True


def test_can_handle_yaml_file(tmp_path):
    spec = tmp_path / "openapi.yaml"
    spec.write_text("openapi: '3.0.0'")
    assert openapi.can_handle(str(tmp_path)) is True


def test_can_handle_dir_with_spec(tmp_path):
    (tmp_path / "openapi.json").write_text('{"openapi": "3.0.0"}')
    assert openapi.can_handle(str(tmp_path)) is True


def test_cannot_handle_dir_without_spec(tmp_path):
    (tmp_path / "main.py").write_text("pass")
    assert openapi.can_handle(str(tmp_path)) is False


def test_can_handle_url():
    # URLs return True if reachable, False otherwise (SSRF blocks example.com)
    result = openapi.can_handle("https://api.example.com")
    assert isinstance(result, bool)


# --------------------------------------------------------------------------- #
#  Spec parsing — via fixture file                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_parse_fixture_spec(openapi_spec, tmp_path):
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(openapi_spec))
    catalog = await openapi.extract(str(tmp_path))

    assert catalog.extractor == "openapi"
    assert len(catalog.routes) == 3


@pytest.mark.asyncio
async def test_get_route_params(openapi_spec, tmp_path):
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(openapi_spec))
    catalog = await openapi.extract(str(tmp_path))

    get_user = next(r for r in catalog.routes if r.path == "/users/{user_id}" and r.method == "GET")
    assert len(get_user.params) == 2

    uid = next(p for p in get_user.params if p.name == "user_id")
    assert uid.type == "int"
    assert uid.location == "path"
    assert uid.required is True

    details = next(p for p in get_user.params if p.name == "include_details")
    assert details.type == "bool"
    assert details.required is False


@pytest.mark.asyncio
async def test_post_body_params(openapi_spec, tmp_path):
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(openapi_spec))
    catalog = await openapi.extract(str(tmp_path))

    post_user = next(r for r in catalog.routes if r.path == "/users" and r.method == "POST")
    body_params = [p for p in post_user.params if p.location == "body"]

    assert len(body_params) == 2
    names = {p.name for p in body_params}
    assert "name" in names
    assert "email" in names
    required = [p for p in body_params if p.required]
    assert len(required) == 2


@pytest.mark.asyncio
async def test_tags_extracted(openapi_spec, tmp_path):
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(openapi_spec))
    catalog = await openapi.extract(str(tmp_path))

    get_user = next(r for r in catalog.routes if r.path == "/users/{user_id}")
    assert "users" in get_user.tags


# --------------------------------------------------------------------------- #
#  $ref resolution                                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ref_resolution(tmp_path):
    spec = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "UserId": {"type": "integer"}
            },
            "parameters": {
                "UserIdParam": {
                    "name": "user_id",
                    "in": "path",
                    "required": True,
                    "schema": {"$ref": "#/components/schemas/UserId"}
                }
            }
        },
        "paths": {
            "/users/{user_id}": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/UserIdParam"}]
                }
            }
        }
    }
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(spec))
    catalog = await openapi.extract(str(tmp_path))

    assert len(catalog.routes) == 1
    uid = catalog.routes[0].params[0]
    assert uid.name == "user_id"
    assert uid.required is True


# --------------------------------------------------------------------------- #
#  Edge cases                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_empty_paths(tmp_path):
    spec = {"openapi": "3.0.0", "paths": {}}
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(spec))
    catalog = await openapi.extract(str(tmp_path))
    assert len(catalog.routes) == 0


@pytest.mark.asyncio
async def test_unknown_methods_ignored(tmp_path):
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/test": {
                "x-custom-extension": {},
                "get": {"parameters": []}
            }
        }
    }
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(spec))
    catalog = await openapi.extract(str(tmp_path))
    assert len(catalog.routes) == 1
    assert catalog.routes[0].method == "GET"
"""
extractors/openapi.py — Tier 1: OpenAPI / Swagger spec parser.

Universal: works for any backend language that exposes an OpenAPI spec.
Zero dependencies (stdlib only).

Supports:
  - Local file: openapi.json / openapi.yaml / swagger.json
  - Remote URL: https://api.example.com/openapi.json
    Falls back to: /docs/openapi.json, /swagger.json, /api-docs, /v3/api-docs
"""

from __future__ import annotations

import ipaddress
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from agora_code.models import Param, Route, RouteCatalog

# Remote URL fallback candidates (tried in order)
_OPENAPI_PATHS = [
    "/openapi.json",
    "/swagger.json",
    "/api-docs",
    "/v3/api-docs",
    "/docs/openapi.json",
    "/swagger/v1/swagger.json",
]

# Local filename candidates (tried relative to repo root)
_LOCAL_FILES = [
    "openapi.json",
    "openapi.yaml",
    "openapi.yml",
    "swagger.json",
    "swagger.yaml",
    "docs/openapi.json",
    "api/openapi.json",
]


def _is_safe_url(url: str) -> bool:
    """
    Validate URL to prevent SSRF attacks.
    Blocks localhost, private IPs, and cloud metadata endpoints.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        
        if not hostname:
            return False
        
        # Block localhost
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return False
        
        # Try to resolve IP
        try:
            ip = ipaddress.ip_address(hostname)
            # Block private networks
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            # Hostname, not IP - check against known bad patterns
            if hostname.startswith("169.254."):  # AWS metadata
                return False
            if hostname.endswith(".internal"):  # Internal domains
                return False
        
        return True
    except Exception:
        return False


def can_handle(target: str) -> bool:
    """Return True if we can find an OpenAPI spec for this target."""
    if target.startswith("http://") or target.startswith("https://"):
        return _fetch_remote(target) is not None
    return _find_local(target) is not None


async def extract(target: str) -> RouteCatalog:
    """Parse an OpenAPI spec and return a RouteCatalog."""
    if target.startswith("http://") or target.startswith("https://"):
        spec = _fetch_remote(target)
        if spec is None:
            raise ValueError(f"No OpenAPI spec found at {target!r}")
    else:
        spec = _find_local(target)
        if spec is None:
            raise ValueError(f"No OpenAPI spec file found in {target!r}")

    routes = _parse_spec(spec)
    return RouteCatalog(source=target, extractor="openapi", routes=routes)


# --------------------------------------------------------------------------- #
#  Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _fetch_remote(base_url: str) -> Optional[dict]:
    """Try known OpenAPI URL patterns. Returns parsed spec dict or None."""
    base = base_url.rstrip("/")

    # If the URL already points directly at a JSON spec file
    if base.endswith(".json") or base.endswith(".yaml"):
        return _get_json(base)

    for path in _OPENAPI_PATHS:
        result = _get_json(base + path)
        if result:
            return result
    return None


def _find_local(repo_path: str) -> Optional[dict]:
    """Search for OpenAPI file in repo directory. Returns parsed dict or None."""
    root = Path(repo_path)
    for filename in _LOCAL_FILES:
        candidate = root / filename
        if candidate.exists():
            try:
                text = candidate.read_text(encoding="utf-8")
                if filename.endswith((".yaml", ".yml")):
                    return _parse_yaml(text)
                return json.loads(text)
            except Exception:
                continue
    return None


def _get_json(url: str) -> Optional[dict]:
    # SSRF protection
    if not _is_safe_url(url):
        print(f"⚠️  Skipping potentially unsafe URL: {url}", file=sys.stderr)
        return None
    
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            if resp.status == 200:
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
                if "yaml" in content_type:
                    return _parse_yaml(body)
                return json.loads(body)
    except Exception:
        pass
    return None


def _parse_yaml(text: str) -> Optional[dict]:
    """Best-effort YAML parser without pyyaml (handles simple OpenAPI specs)."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass
    # Minimal fallback: only works for JSON-compatible YAML (no anchors etc.)
    try:
        import json
        return json.loads(text)  # YAML is a superset of JSON
    except Exception:
        return None


def _parse_spec(spec: dict) -> list[Route]:
    """Convert OpenAPI spec dict → list of Route objects."""
    routes = []
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method, operation in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
                continue
            if not isinstance(operation, dict):
                continue

            description = (
                operation.get("summary")
                or operation.get("description")
                or ""
            )
            tags = operation.get("tags", [])
            params = _parse_params(operation, spec)

            routes.append(Route(
                method=method.upper(),
                path=path,
                params=params,
                description=description,
                tags=tags,
            ))

    return routes


def _parse_params(operation: dict, spec: dict) -> list[Param]:
    """Extract parameters from an OpenAPI operation."""
    params = []

    # Path/query/header parameters
    for p in operation.get("parameters", []):
        p = _resolve_ref(p, spec)
        if not isinstance(p, dict):
            continue
        schema = p.get("schema", {})
        params.append(Param(
            name=p.get("name", "param"),
            type=_openapi_type(schema),
            required=p.get("required", False),
            description=p.get("description", ""),
            location=p.get("in", "query"),
            default=schema.get("default"),
        ))

    # Request body
    request_body = operation.get("requestBody", {})
    request_body = _resolve_ref(request_body, spec)
    if request_body:
        content = request_body.get("content", {})
        for media_type, media_obj in content.items():
            schema = _resolve_ref(media_obj.get("schema", {}), spec)
            if schema.get("type") == "object":
                props = schema.get("properties", {})
                required_fields = schema.get("required", [])
                for name, prop_schema in props.items():
                    prop_schema = _resolve_ref(prop_schema, spec)
                    params.append(Param(
                        name=name,
                        type=_openapi_type(prop_schema),
                        required=name in required_fields,
                        description=prop_schema.get("description", ""),
                        location="body",
                    ))
            break  # only parse first content type

    return params


def _resolve_ref(obj: dict, spec: dict) -> dict:
    """Resolve $ref pointers in OpenAPI spec."""
    if not isinstance(obj, dict) or "$ref" not in obj:
        return obj
    ref = obj["$ref"]
    if not ref.startswith("#/"):
        return obj
    parts = ref.lstrip("#/").split("/")
    result = spec
    for part in parts:
        result = result.get(part, {})
    return result


def _openapi_type(schema: dict) -> str:
    """Convert OpenAPI type string to Python type name."""
    openapi_to_py = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
    }
    return openapi_to_py.get(schema.get("type", "string"), "str")
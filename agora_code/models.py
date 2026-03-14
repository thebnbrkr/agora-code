"""
models.py — Core data classes for agora-code.

These are the universal output types produced by all extractors
(OpenAPI, AST, LLM, regex). Callers never care which tier ran.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
#  Primitive types                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class Param:
    """A single parameter for an API route."""
    name: str
    type: str = "any"          # "str" | "int" | "float" | "bool" | "any"
    required: bool = False
    description: str = ""
    location: str = "query"    # "query" | "path" | "body" | "header"
    default: Any = None


@dataclass
class Route:
    """
    One API endpoint extracted from a codebase.

    Produced by every extractor tier — OpenAPI, AST, LLM, regex.
    """
    method: str                     # "GET" | "POST" | "PUT" | "DELETE" | "PATCH"
    path: str                       # "/products/{id}"
    params: List[Param] = field(default_factory=list)
    description: str = ""           # from docstring, OpenAPI summary, or LLM
    raw_code: str = ""              # source snippet (used by LLM tier)
    tags: List[str] = field(default_factory=list)
    response_schema: Optional[Dict] = None   # best-effort output type info

    @property
    def tool_name(self) -> str:
        """Slug used as the MCP tool name. e.g. 'get_products_id'"""
        slug = re.sub(r"[{}]", "", self.path)          # remove path param braces
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", slug)    # non-alnum → underscore
        slug = slug.strip("_").lower()
        return f"{self.method.lower()}_{slug}"

    def to_dict(self) -> Dict:
        return {
            "method": self.method,
            "path": self.path,
            "tool_name": self.tool_name,
            "description": self.description,
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    "location": p.location,
                    "description": p.description,
                }
                for p in self.params
            ],
            "tags": self.tags,
        }


# --------------------------------------------------------------------------- #
#  RouteCatalog — the output of scan()                                         #
# --------------------------------------------------------------------------- #

@dataclass
class RouteCatalog:
    """
    The result of scanning a codebase or API spec.

    source:    what was scanned (path or URL)
    extractor: which tier ran: "openapi" | "ast" | "llm" | "regex"
    edition:   "community" (default) | "enterprise"
    routes:    all discovered routes
    """
    source: str
    extractor: str
    routes: List[Route]
    edition: str = "community"
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    #  Convenience                                                         #
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self.routes)

    def filter(self, method: str = None, tag: str = None) -> "RouteCatalog":
        """Return a new catalog with only matching routes."""
        routes = self.routes
        if method:
            routes = [r for r in routes if r.method.upper() == method.upper()]
        if tag:
            routes = [r for r in routes if tag in r.tags]
        return RouteCatalog(
            source=self.source,
            extractor=self.extractor,
            routes=routes,
            edition=self.edition,
            metadata=self.metadata,
        )

    def summary(self) -> str:
        method_counts = {}
        for r in self.routes:
            method_counts[r.method] = method_counts.get(r.method, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(method_counts.items())]
        return (
            f"RouteCatalog: {len(self.routes)} routes "
            f"({', '.join(parts)}) | "
            f"extractor={self.extractor} | "
            f"edition={self.edition}"
        )

    # ------------------------------------------------------------------ #
    #  Exporters                                                           #
    # ------------------------------------------------------------------ #

    def to_mcp_tools(self) -> List[Dict]:
        """
        Convert catalog to MCP tool definitions.

        Each route becomes one tool with:
          - name: route.tool_name
          - description: route.description
          - inputSchema: JSON Schema built from route.params
        """
        tools = []
        for route in self.routes:
            properties = {}
            required = []

            for p in route.params:
                json_type = _py_type_to_json(p.type)
                prop = {"type": json_type}
                if p.description:
                    prop["description"] = p.description
                if p.default is not None:
                    prop["default"] = p.default
                properties[p.name] = prop
                if p.required:
                    required.append(p.name)

            tool = {
                "name": route.tool_name,
                "description": _build_tool_description(route),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    **({} if not required else {"required": required}),
                },
                "_meta": {
                    "method": route.method,
                    "path": route.path,
                },
            }
            tools.append(tool)
        return tools

    def to_openapi(self, title: str = "Auto-generated API", version: str = "1.0.0") -> Dict:
        """
        Generate an OpenAPI 3.0 spec from the catalog.

        Useful when agora-code built the catalog from AST/regex
        and no spec previously existed.
        """
        paths: Dict[str, Any] = {}
        for route in self.routes:
            path_key = route.path
            method_key = route.method.lower()

            query_params = [p for p in route.params if p.location in ("query", "path")]
            body_params = [p for p in route.params if p.location == "body"]

            operation: Dict[str, Any] = {
                "summary": route.description or route.tool_name,
                "operationId": route.tool_name,
                "tags": route.tags or [],
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "500": {"description": "Server error"},
                },
            }

            if query_params:
                operation["parameters"] = [
                    {
                        "name": p.name,
                        "in": p.location,
                        "required": p.required,
                        "schema": {"type": _py_type_to_json(p.type)},
                        "description": p.description,
                    }
                    for p in query_params
                ]

            if body_params:
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    p.name: {"type": _py_type_to_json(p.type)}
                                    for p in body_params
                                },
                            }
                        }
                    },
                }

            paths.setdefault(path_key, {})[method_key] = operation

        return {
            "openapi": "3.0.0",
            "info": {"title": title, "version": version},
            "paths": paths,
        }

    def to_json(self) -> str:
        return json.dumps(
            {"source": self.source, "extractor": self.extractor,
             "edition": self.edition, "routes": [r.to_dict() for r in self.routes]},
            indent=2,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "RouteCatalog":
        """Deserialize a RouteCatalog from JSON produced by to_json()."""
        data = json.loads(json_str)
        routes = []
        for r in data.get("routes", []):
            params = [
                Param(
                    name=p["name"],
                    type=p.get("type", "any"),
                    required=p.get("required", False),
                    location=p.get("location", "query"),
                    description=p.get("description", ""),
                )
                for p in r.get("params", [])
            ]
            routes.append(Route(
                method=r["method"],
                path=r["path"],
                params=params,
                description=r.get("description", ""),
                tags=r.get("tags", []),
            ))
        return cls(
            source=data["source"],
            extractor=data["extractor"],
            routes=routes,
            edition=data.get("edition", "community"),
        )


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _py_type_to_json(py_type: str) -> str:
    return {
        "str": "string", "int": "integer", "float": "number",
        "bool": "boolean", "list": "array", "dict": "object",
    }.get(py_type.lower(), "string")


def _build_tool_description(route: "Route") -> str:
    """
    Build an enriched MCP tool description with USE THIS WHEN: guidance.

    Inspired by the MCP tool description pattern for rich AI-readable metadata.
    Tells the agent WHEN to call this tool proactively, not just what it does.
    """
    base = route.description or f"{route.method} {route.path}"

    # Build USE THIS WHEN based on method + path
    method = route.method.upper()
    path = route.path

    # Infer a natural-language verb from method
    verb_map = {
        "GET": "retrieve", "POST": "create", "PUT": "update",
        "PATCH": "update", "DELETE": "delete",
    }
    verb = verb_map.get(method, "call")

    # Build resource name from path (e.g. /products/{id} → products)
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
    resource = parts[-1] if parts else "resource"

    # Compose USE THIS WHEN section
    when_lines = [f"- User wants to {verb} {resource}"]
    if method == "GET" and "{" in path:
        when_lines.append(f"- User asks for a specific {resource} by ID")
    elif method == "GET":
        when_lines.append(f"- User asks to list, fetch, or show {resource}")
    elif method in ("POST",):
        when_lines.append(f"- User wants to add or submit a new {resource}")
    elif method in ("PUT", "PATCH"):
        when_lines.append(f"- User wants to modify an existing {resource}")
    elif method == "DELETE":
        when_lines.append(f"- User wants to remove a {resource}")

    when_section = "\n".join(when_lines)
    return f"{base}\n\nUSE THIS WHEN:\n{when_section}"

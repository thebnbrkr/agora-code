"""
agora-code: Scan any codebase. Turn it into an agent.

Quick start:
    from agora_code import scan
    from agora_code.agent import MCPServer

    catalog = await scan("./my-api")
    server = MCPServer(catalog, base_url="http://localhost:8000")
    await server.serve()   # stdio MCP server — plug into Claude Desktop / Cline
"""

from agora_code.models import Route, Param, RouteCatalog
from agora_code.scanner import scan
from agora_code.agent import MCPServer, APICallNode

__version__ = "0.2.3"
__all__ = ["Route", "Param", "RouteCatalog", "scan", "MCPServer", "APICallNode"]

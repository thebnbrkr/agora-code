"""
cli.py — agora-code command line interface.

Commands:
    agora-code scan ./my-api         — discover all routes, print table
    agora-code serve ./my-api        — start MCP server
    agora-code stats ./my-api        — show API call stats from memory
    agora-code auth ./my-api         — configure auth interactively

Requires: pip install agora-code  (click is a dependency)
Rich output via 'rich' if installed, plain fallback otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import click


# --------------------------------------------------------------------------- #
#  CLI group                                                                   #
# --------------------------------------------------------------------------- #

@click.group()
@click.version_option(package_name="agora-code")
def main():
    """agora-code — Turn any API into a memory-aware agent."""
    pass


# --------------------------------------------------------------------------- #
#  scan                                                                        #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--output", "-o", default=None, help="Save routes to JSON file")
@click.option("--use-llm", is_flag=True, default=False, help="Enable LLM extractor (costs money)")
@click.option("--llm-provider", default="openai", type=click.Choice(["openai", "gemini"]),
              help="LLM provider for Tier 3 extraction")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "mcp"]),
              help="Output format")
@click.option("--enterprise", is_flag=True, default=False, help="Enterprise edition mode")
def scan(target, output, use_llm, llm_provider, fmt, enterprise):
    """Scan a codebase or URL and discover all API routes.

    TARGET can be a local directory path or a remote URL:

    \b
    agora-code scan ./my-fastapi-app
    agora-code scan https://api.example.com
    agora-code scan ./my-app --output routes.json
    agora-code scan ./node-app --use-llm
    """
    from agora_code.scanner import scan as do_scan

    _echo(f"🔍 Scanning {target!r}...")

    catalog = asyncio.run(do_scan(
        target,
        use_llm=use_llm,
        llm_provider=llm_provider,
        edition="enterprise" if enterprise else "community",
    ))

    if len(catalog) == 0:
        _echo("⚠️  No routes found. Try --use-llm for non-Python/non-OpenAPI repos.")
        return

    _echo(f"✅ Found {len(catalog)} routes via {catalog.extractor} extractor\n")

    if fmt == "json":
        click.echo(catalog.to_json())
    elif fmt == "mcp":
        click.echo(json.dumps(catalog.to_mcp_tools(), indent=2))
    else:
        _print_routes_table(catalog)

    if output:
        Path(output).write_text(catalog.to_json(), encoding="utf-8")
        _echo(f"\n💾 Saved to {output}")

    _echo(f"\nNext step:  agora-code serve {target}")


# --------------------------------------------------------------------------- #
#  serve                                                                       #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--url", "-u", required=True, help="Base URL of the live API")
@click.option("--use-llm", is_flag=True, default=False)
@click.option("--llm-provider", default="openai", type=click.Choice(["openai", "gemini"]))
@click.option("--auth-token", envvar="AGORA_AUTH_TOKEN", default=None,
              help="Bearer token (or set AGORA_AUTH_TOKEN env var)")
@click.option("--auth-type", default="bearer",
              type=click.Choice(["bearer", "api-key", "basic", "none"]))
@click.option("--memory/--no-memory", default=True, help="Enable agora-mem memory layer")
@click.option("--db-path", default="./agora_agent_memory.db",
              help="SQLite path for memory (community edition)")
@click.option("--enterprise", is_flag=True, default=False)
def serve(target, url, use_llm, llm_provider, auth_token, auth_type, memory, db_path, enterprise):
    """Start an MCP server for your API — plug into Claude Desktop or Cursor.

    \b
    agora-code serve ./my-api --url http://localhost:8000
    agora-code serve https://api.example.com --url https://api.example.com

    Add to Claude Desktop config (~/.config/claude/config.json):

    \b
    {
      "mcpServers": {
        "my-api": {
          "command": "agora-code",
          "args": ["serve", "./my-api", "--url", "http://localhost:8000"]
        }
      }
    }
    """
    from agora_code.scanner import scan as do_scan
    from agora_code.agent import MCPServer

    _echo(f"🔍 Scanning {target!r}...", err=True)
    catalog = asyncio.run(do_scan(
        target, use_llm=use_llm, llm_provider=llm_provider,
        edition="enterprise" if enterprise else "community",
    ))
    _echo(f"✅ {len(catalog)} routes loaded from {catalog.extractor} extractor", err=True)

    # Auth config
    auth = {}
    if auth_type != "none" and auth_token:
        auth = {"type": auth_type, "token": auth_token}
    elif auth_type != "none":
        _echo("⚠️  No auth token set. Set AGORA_AUTH_TOKEN or pass --auth-token", err=True)

    # Memory (optional - only if agora-mem is installed)
    agent_memory = None
    if memory:
        try:
            from agora_mem import MemoryStore
            from agora_code.memory_layer import AgentMemory
            store = MemoryStore(storage="sqlite", db_path=db_path)
            agent_memory = AgentMemory(store)
            _echo(f"🧠 Memory enabled (SQLite: {db_path})", err=True)
        except ImportError:
            _echo("⚠️  agora-mem not installed — running without memory. "
                  "pip install agora-code[memory]", err=True)

    #  FIX: Only pass memory if MCPServer supports it
    # Check if MCPServer accepts memory parameter
    from inspect import signature
    mcp_params = signature(MCPServer.__init__).parameters
    
    if 'memory' in mcp_params and agent_memory is not None:
        server = MCPServer(
            catalog=catalog,
            base_url=url,
            memory=agent_memory,
            auth=auth,
            edition="enterprise" if enterprise else "community",
        )
    else:
        # Memory not supported yet or not available
        server = MCPServer(
            catalog=catalog,
            base_url=url,
            auth=auth,
            edition="enterprise" if enterprise else "community",
        )

    _echo(f"🚀 MCP server ready ({len(catalog)} tools)", err=True)
    asyncio.run(server.serve())


# --------------------------------------------------------------------------- #
#  stats                                                                       #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--db-path", default="./agora_agent_memory.db")
@click.option("--window", default=24, help="Time window in hours for pattern detection")
def stats(target, db_path, window):
    """Show API call stats and patterns from memory.

    \b
    agora-code stats ./my-api
    agora-code stats ./my-api --window 48
    """
    try:
        from agora_mem import MemoryStore
        from agora_code.memory_layer import AgentMemory
    except ImportError:
        _echo("❌ agora-mem not installed. Run: pip install agora-code[memory]")
        sys.exit(1)

    store = MemoryStore(storage="sqlite", db_path=db_path)
    memory = AgentMemory(store)

    async def _run():
        from agora_code.scanner import scan as do_scan
        catalog = await do_scan(target)

        _echo(f"\n📊 API Stats — {target}\n")

        for route in catalog.routes[:20]:
            s = await memory.get_endpoint_stats(route.method, route.path)
            if s["total_calls"] == 0:
                continue
            success_pct = int(s["success_rate"] * 100) if s["success_rate"] else 0
            latency = f"{s['avg_latency_ms']:.0f}ms" if s["avg_latency_ms"] else "—"
            _echo(
                f"  {route.method:6} {route.path:40} "
                f"{s['total_calls']:4} calls  "
                f"{success_pct:3}% ok  "
                f"{latency}"
            )

        _echo("\n🔍 Patterns detected:\n")
        patterns = await memory.detect_patterns(time_window_hours=window)
        for p in patterns:
            _echo(f"  {p}")

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
#  auth                                                                        #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--type", "auth_type",
              type=click.Choice(["bearer", "api-key", "basic", "none"]),
              default=None)
@click.option("--token", default=None, help="Token value (skip prompt)")
def auth(target, auth_type, token):
    """Configure authentication for API calls.

    \b
    agora-code auth ./my-api
    agora-code auth ./my-api --type bearer --token mytoken123
    """
    config_path = Path(target) / ".agora-code" / "auth.json"

    if not auth_type:
        auth_type = click.prompt(
            "Auth type",
            type=click.Choice(["bearer", "api-key", "basic", "none"]),
            default="bearer",
        )

    if auth_type == "none":
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"type": "none"}), encoding="utf-8")
        _echo("✅ Auth disabled")
        return

    if not token:
        token = click.prompt("Token / API key", hide_input=True)

    config = {"type": auth_type, "token": token}

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _echo(f"✅ Auth saved to {config_path}")
    _echo("   Add to .gitignore: .agora-code/auth.json")


# --------------------------------------------------------------------------- #
#  chat                                                                        #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--url", "-u", required=True, help="Base URL of the live API")
@click.option("--use-llm", is_flag=True, default=False)
@click.option("--level", default="summary",
              type=click.Choice(["index", "summary", "detail", "full"]),
              help="TLDR compression level for context (default: summary)")
@click.option("--auth-token", envvar="AGORA_AUTH_TOKEN", default=None)
@click.option("--auth-type", default="bearer",
              type=click.Choice(["bearer", "api-key", "basic", "none"]))
def chat(target, url, use_llm, level, auth_token, auth_type):
    """Start an interactive chat session to talk to your API in natural language.

    \b
    agora-code chat ./my-api --url http://localhost:8000
    agora-code chat https://api.example.com --url https://api.example.com --level index
    """
    try:
        import openai  # noqa: F401
    except ImportError:
        _echo("❌ openai not installed. Run: pip install agora-code[llm]")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _echo("❌ OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    from agora_code.scanner import scan as do_scan
    from agora_code.tldr import compress_catalog

    _echo(f"🔍 Scanning {target!r} ({level} compression)...")
    catalog = asyncio.run(do_scan(target, use_llm=use_llm))

    if len(catalog) == 0:
        _echo("⚠️  No routes found. Try --use-llm.")
        return

    # Build compressed context for the LLM
    tldr = compress_catalog(catalog, level=level)
    _echo(f"✅ {len(catalog)} routes loaded — context: {len(tldr.split())} words\n")
    _echo(tldr)
    _echo("\n" + "─" * 60)
    _echo("💬 Chat with your API. Type 'exit' to quit.\n")

    auth = {}
    if auth_type != "none" and auth_token:
        auth = {"type": auth_type, "token": auth_token}

    from agora_code.agent import MCPServer
    server = MCPServer(catalog=catalog, base_url=url, auth=auth)

    async def _chat_loop():
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        system_prompt = (
            f"You are an assistant that helps call an API.\n\n"
            f"Available routes:\n{tldr}\n\n"
            "When the user asks to do something, identify the right route "
            "and call the appropriate tool. Be concise."
        )
        messages = [{"role": "system", "content": system_prompt}]
        tools = catalog.to_mcp_tools()

        while True:
            try:
                user_input = click.prompt("You", prompt_suffix="> ")
            except (EOFError, KeyboardInterrupt):
                _echo("\n👋 Bye!")
                break

            if user_input.strip().lower() in ("exit", "quit", "q"):
                _echo("👋 Bye!")
                break

            messages.append({"role": "user", "content": user_input})

            # Build OpenAI function definitions from MCP tools
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["inputSchema"],
                    },
                }
                for t in tools
            ]

            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
            )
            msg = resp.choices[0].message

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    import json as _json
                    args = _json.loads(tc.function.arguments or "{}")
                    _echo(f"  🔧 Calling {tool_name}({args})")
                    result, _ = await server._nodes[tool_name].run(args) if tool_name in server._nodes else ({"body": {"error": "unknown tool"}, "status": 404}, [])
                    result_text = _json.dumps(result.get("body", {}), indent=2)[:1000]
                    messages.append({"role": "assistant", "content": None, "tool_calls": [tc.model_dump()]})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

                # Follow-up response after tool call
                follow = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                )
                reply = follow.choices[0].message.content or ""
            else:
                reply = msg.content or ""

            _echo(f"\n🤖 {reply}\n")
            messages.append({"role": "assistant", "content": reply})

    asyncio.run(_chat_loop())


# --------------------------------------------------------------------------- #
#  status                                                                      #
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--db-path", default="./agora_agent_memory.db")
def status(db_path):
    """Show what's been scanned and cached.

    \b
    agora-code status
    """
    try:
        from agora_mem import MemoryStore
        from agora_code.memory_layer import AgentMemory
    except ImportError:
        _echo("❌ agora-mem not installed. Run: pip install agora-code[memory]")
        sys.exit(1)

    store = MemoryStore(storage="sqlite", db_path=db_path)
    memory = AgentMemory(store)

    async def _run():
        session_ids = await store.list_sessions()
        scan_sessions = [s for s in session_ids if s.startswith("scan:")]

        if not scan_sessions:
            _echo("📭 No scans cached yet. Run: agora-code scan <target>")
            return

        _echo(f"\n📦 Cached scans ({len(scan_sessions)}):\n")
        for sid in scan_sessions:
            cache = await memory.load_scan_cache(sid.removeprefix("scan:"))
            if cache:
                import time as _time
                age = _time.time() - cache.get("scanned_at", 0)
                age_str = f"{int(age // 3600)}h ago" if age > 3600 else f"{int(age // 60)}m ago"
                _echo(
                    f"  ✅ {cache['target']} — "
                    f"{cache['route_count']} routes via {cache['extractor']} "
                    f"({age_str})"
                )

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
#  inject                                                                      #
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--level", default="summary",
              type=click.Choice(["index", "summary", "detail", "full"]),
              help="Compression level")
@click.option("--quiet", is_flag=True, default=False,
              help="Plain output (no headers, for Claude hook injection)")
@click.option("--db-path", default="./agora_agent_memory.db")
def inject(level, quiet, db_path):
    """Inject compressed route context (used by Claude hooks at session start).

    \b
    agora-code inject                  # prints summary-level context
    agora-code inject --level index    # ultra-compact
    agora-code inject --quiet          # plain text for hook injection
    """
    try:
        from agora_mem import MemoryStore
        from agora_code.memory_layer import AgentMemory
    except ImportError:
        if not quiet:
            _echo("⚠️  agora-mem not installed — no cached context available.")
        return

    store = MemoryStore(storage="sqlite", db_path=db_path)
    memory = AgentMemory(store)

    async def _run():
        session_ids = await store.list_sessions()
        scan_sessions = [s for s in session_ids if s.startswith("scan:")]

        if not scan_sessions:
            if not quiet:
                _echo("📭 No scans cached. Run: agora-code scan <target> first.")
            return

        parts = []
        if not quiet:
            parts.append("<agora-code-context>\n")

        for sid in scan_sessions:
            cache = await memory.load_scan_cache(sid.removeprefix("scan:"))
            if cache:
                tldr_key = f"tldr_{level}"
                if level == "full":
                    tldr = cache.get("routes_json", "")
                else:
                    tldr = cache.get(tldr_key, cache.get("tldr_summary", ""))
                if tldr:
                    parts.append(tldr)

        if not quiet:
            parts.append("\n</agora-code-context>")

        click.echo("\n".join(parts))

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
#  state                                                                       #
# --------------------------------------------------------------------------- #

@main.group()
def state():
    """Manage agora-code session state."""
    pass


@state.command(name="save")
@click.option("--db-path", default="./agora_agent_memory.db")
def state_save(db_path):
    """Save current session state (called by PreCompact Claude hook)."""
    # Re-scan current directory and refresh cache
    from agora_code.scanner import scan as do_scan

    async def _run():
        try:
            catalog = await do_scan(".", use_llm=False)
            if len(catalog) == 0:
                return
            from agora_mem import MemoryStore
            from agora_code.memory_layer import AgentMemory
            store = MemoryStore(storage="sqlite", db_path=db_path)
            memory = AgentMemory(store)
            await memory.store_scan_result(".", catalog, ttl_seconds=0)  # no expiry
        except Exception:
            pass  # Hooks should never crash Claude

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _echo(msg: str, err: bool = False) -> None:
    """Print with rich if available, plain otherwise."""
    try:
        from rich import print as rprint
        rprint(msg, file=sys.stderr if err else sys.stdout)
    except ImportError:
        click.echo(msg, err=err)


def _print_routes_table(catalog) -> None:
    """Print routes as a formatted table."""
    try:
        from rich.table import Table
        from rich.console import Console
        console = Console()
        table = Table(title=f"Routes — {catalog.source}")
        table.add_column("Method", style="cyan", width=8)
        table.add_column("Path", style="magenta")
        table.add_column("Params", style="yellow")
        table.add_column("Description", style="white")
        for route in catalog.routes:
            param_str = ", ".join(
                f"{p.name}:{p.type}{'*' if p.required else ''}"
                for p in route.params[:3]
            )
            table.add_row(
                route.method,
                route.path,
                param_str,
                route.description[:50] if route.description else "",
            )
        console.print(table)
    except ImportError:
        # Plain fallback
        click.echo(f"{'METHOD':<8} {'PATH':<40} {'PARAMS'}")
        click.echo("-" * 70)
        for route in catalog.routes:
            param_str = ", ".join(p.name for p in route.params[:3])
            click.echo(f"{route.method:<8} {route.path:<40} {param_str}")


if __name__ == "__main__":
    main()
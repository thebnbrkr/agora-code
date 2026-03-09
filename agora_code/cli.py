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
    """Show current session state and recent call stats.

    \b
    agora-code status
    """
    from agora_code.session import load_session
    from agora_code.tldr import compress_session, estimate_tokens

    session = load_session()
    if not session:
        _echo("📭 No active session. Start one with:")
        _echo("   agora-code checkpoint --goal \"What you're trying to do\"")
    else:
        _echo("\n" + "═" * 60)
        _echo(f"🗂  SESSION: {session.get('session_id', 'unknown')}")
        _echo("═" * 60)
        _echo(compress_session(session, level="detail"))

    # Also show VectorStore stats if available
    try:
        from agora_code.vector_store import get_store
        stats = get_store().get_stats()
        _echo(f"\n🧠 Memory: {stats['sessions']} sessions, "
              f"{stats['learnings']} learnings, "
              f"{stats['api_calls']} API calls logged"
              f"  [vector search: {'on' if stats['vector_search'] else 'off (install sqlite-vec)'}]")
    except Exception:
        pass


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

# --------------------------------------------------------------------------- #
#  checkpoint                                                                  #
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--goal", default=None, help="What you're trying to accomplish")
@click.option("--hypothesis", default=None, help="Current working theory")
@click.option("--action", default=None, help="What you're doing right now")
@click.option("--context", default=None, help="Free-text project context or notes")
@click.option("--api", default=None, help="Base URL of the API being tested")
@click.option("--next", "next_step", default=None, multiple=True, help="Next steps (repeatable)")
@click.option("--blocker", default=None, multiple=True, help="Blockers (repeatable)")
@click.option("--file", "file_changed", default=None, multiple=True,
              help="File you changed, optionally with note: 'auth.py:added retry logic'")
def checkpoint(goal, hypothesis, action, context, api, next_step, blocker, file_changed):
    """Save current session state to .agora-code/session.json.

    \b
    Works for any project — API or non-API:

    agora-code checkpoint --goal "Refactor auth module"
    agora-code checkpoint --hypothesis "SessionManager needs lock"
    agora-code checkpoint --action "Adding retry logic to validate()"
    agora-code checkpoint --file "auth.py:added retry" --file "tests/test_auth.py:updated tests"
    agora-code checkpoint --next "Write test for edge case" --blocker "Waiting for review"
    """
    from agora_code.session import load_session, new_session, update_session

    updates: dict = {}
    if goal:       updates["goal"] = goal
    if hypothesis: updates["hypothesis"] = hypothesis
    if action:     updates["current_action"] = action
    if context:    updates["context"] = context
    if api:        updates["api_base_url"] = api
    if next_step:  updates["next_steps"] = list(next_step)
    if blocker:    updates["blockers"] = [b for b in blocker]
    if file_changed:
        files = []
        for f in file_changed:
            if ":" in f:
                fname, what = f.split(":", 1)
                files.append({"file": fname.strip(), "what": what.strip()})
            else:
                files.append({"file": f.strip(), "what": ""})
        updates["files_changed"] = files

    session = update_session(updates)
    _echo(f"✅ Session saved: {session['session_id']}")
    _echo(f"   Goal: {session.get('goal') or '(none)'} | Status: {session.get('status', 'in_progress')}")


# --------------------------------------------------------------------------- #
#  complete                                                                    #
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--summary", default=None, help="What you accomplished")
@click.option("--outcome", default="success", type=click.Choice(["success", "partial", "abandoned"]),
              help="How the session ended")
def complete(summary, outcome):
    """Archive the current session and store it in memory.

    \b
    agora-code complete --summary "Refactored auth, added retry logic"
    agora-code complete --outcome partial
    """
    from agora_code.session import archive_session

    session = archive_session(summary=summary, outcome=outcome)
    _echo(f"✅ Session '{session.get('session_id')}' archived ({outcome}).")
    if summary:
        _echo(f"   Summary: {summary}")
    _echo("   Session stored in memory for future recall.")


# --------------------------------------------------------------------------- #
#  inject                                                                      #
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--level", default="summary",
              type=click.Choice(["index", "summary", "detail", "full"]),
              help="Compression level (default: summary ~200 tokens)")
@click.option("--token-budget", default=2000, help="Max tokens for auto-level picking")
@click.option("--raw", is_flag=True, default=False, help="Print raw session JSON")
def inject(level, token_budget, raw):
    """Print compressed session context for injection into any coding agent.

    \b
    Use with Claude Code hooks (.claude/settings.json):
        {"hooks": {"PreToolUse": [{"command": "agora-code inject"}]}}

    Or pipe directly:
        agora-code inject | pbcopy   # paste into any chat
        agora-code inject --level detail
        agora-code inject --raw      # full session JSON
    """
    from agora_code.session import load_session_if_recent, load_session
    from agora_code.tldr import compress_session, auto_compress_session, session_restored_banner

    session = load_session_if_recent(max_age_hours=48) or load_session()
    if not session:
        # Silently exit — no session to inject (don't pollute agent context)
        return

    if raw:
        import json as _json
        click.echo(_json.dumps(session, indent=2))
        return

    if level == "auto" or token_budget:
        text = auto_compress_session(session, token_budget=token_budget)
    else:
        text = compress_session(session, level=level)

    click.echo(text)   # plain echo — no emoji, goes straight to agent context



# --------------------------------------------------------------------------- #
#  restore                                                                     #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("session_id", required=False)
def restore(session_id):
    """Restore a past session as the active session.

    \b
    agora-code restore                                  # list sessions
    agora-code restore 2026-03-08-debug-post-users      # restore specific
    """
    from agora_code.vector_store import get_store
    from agora_code.session import save_session
    from agora_code.tldr import compress_session

    vs = get_store()

    if not session_id:
        # List recent sessions
        sessions = vs.list_sessions(limit=10)
        if not sessions:
            _echo("📭 No sessions in memory yet.")
            return
        _echo("\n📚 Recent sessions (use restore <session_id>):\n")
        for s in sessions:
            _echo(f"  {s['status'][:1].upper()}  {s['session_id']:<45} {s['last_active'][:10]}  {s.get('goal','')[:40]}")
        return

    data = vs.load_session(session_id)
    if not data:
        _echo(f"❌ Session '{session_id}' not found.")
        sys.exit(1)

    # Restore: mark in_progress, resave to JSON
    data["status"] = "in_progress"
    save_session(data)
    _echo(f"✅ Session '{session_id}' restored as active.")
    _echo("")
    _echo(compress_session(data, level="summary"))


# --------------------------------------------------------------------------- #
#  learn                                                                       #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("finding")
@click.option("--endpoint", default=None, help="e.g. 'POST /users'")
@click.option("--api", default=None, help="Base URL of the API")
@click.option("--evidence", default=None, help="Supporting evidence or example")
@click.option("--confidence", default="confirmed",
              type=click.Choice(["confirmed", "likely", "hypothesis"]))
@click.option("--tags", default=None, help="Comma-separated tags")
def learn(finding, endpoint, api, evidence, confidence, tags):
    """Store a permanent learning about an API.

    \b
    agora-code learn "POST /users rejects + in emails" --tags email,validation
    agora-code learn "Rate limit is 100 req/min" --endpoint "GET /data" --confidence confirmed
    """
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_embedding
    from agora_code.session import load_session

    session = load_session()
    session_id = session.get("session_id") if session else None

    method = path = None
    if endpoint:
        parts = endpoint.strip().split(None, 1)
        method = parts[0].upper() if len(parts) >= 1 else None
        path   = parts[1] if len(parts) >= 2 else None

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    embed = get_embedding(finding + " " + (evidence or ""))

    lid = get_store().store_learning(
        finding=finding,
        session_id=session_id,
        api_base_url=api,
        endpoint_method=method,
        endpoint_path=path,
        evidence=evidence,
        confidence=confidence,
        tags=tag_list,
        embedding=embed,
    )
    _echo(f"✅ Learning stored (id: {lid[:8]}…)")
    if embed is None:
        _echo("   ⚠️  No embedding generated — set OPENAI_API_KEY for semantic recall.")
        _echo("   Keyword search will still work.")


# --------------------------------------------------------------------------- #
#  recall                                                                      #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=5, help="Max results")
def recall(query, limit):
    """Search your learnings knowledge base semantically.

    \b
    agora-code recall "email validation"
    agora-code recall "rate limit" --limit 10
    """
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_query_embedding

    vs = get_store()
    embed = get_query_embedding(query)

    if embed:
        results = vs.search_learnings_semantic(embed, k=limit)
        mode = "semantic"
    else:
        results = []
        mode = None

    if not results:
        results = vs.search_learnings_keyword(query, k=limit)
        mode = "keyword"

    if not results:
        _echo(f"📭 No learnings match '{query}'.")
        _echo("   Store one with: agora-code learn \"your finding\"")
        return

    _echo(f"\n🔍 {len(results)} result(s) [{mode} search]:\n")
    for i, r in enumerate(results, 1):
        ep = ""
        if r.get("endpoint_method") and r.get("endpoint_path"):
            ep = f"  [{r['endpoint_method']} {r['endpoint_path']}]"
        conf_emoji = {"confirmed": "✓", "likely": "~", "hypothesis": "?"}.get(r.get("confidence", ""), "")
        tags = ", ".join(r.get("tags") or [])
        _echo(f"  {i}. {conf_emoji} {r['finding']}{ep}")
        if r.get("evidence"):
            _echo(f"     Evidence: {r['evidence']}")
        if tags:
            _echo(f"     Tags: {tags}")
        _echo("")



# --------------------------------------------------------------------------- #
#  memory-server                                                               #
# --------------------------------------------------------------------------- #

@main.command("memory-server")
def memory_server():
    """Start a project-agnostic MCP server for day-to-day coding.

    \b
    Exposes 6 session/memory tools to any AI coding assistant:
      get_session_context  — what you're working on (auto-injected on start)
      save_checkpoint      — save goal, hypothesis, files changed
      store_learning       — permanent findings across all projects
      recall_learnings     — search past findings semantically
      complete_session     — archive session to long-term memory
      get_memory_stats     — storage stats

    No target directory or running API needed.

    \b
    Add to Antigravity / Claude Desktop (.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "agora-memory": {
          "command": "agora-code",
          "args": ["memory-server"]
        }
      }
    }
    """
    from agora_code.memory_server import serve_memory
    asyncio.run(serve_memory())


# --------------------------------------------------------------------------- #
#  agentify                                                                    #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--llm-provider", default="auto", type=click.Choice(["auto", "claude", "openai", "gemini"]))
@click.option("--llm-model", default=None, help="Override default model for workflow detection")
@click.option("--output", "-o", default=None, help="Directory to save generated flow code")
@click.option("--show-mermaid", is_flag=True, default=False, help="Print Mermaid DAG diagram")
def agentify(target, llm_provider, llm_model, output, show_mermaid):
    """Scan a repo and auto-generate workflows from its API routes.

    \b
    agora-code agentify ./my-api
    agora-code agentify ./my-api --output ./workflows --show-mermaid
    agora-code agentify https://api.example.com --llm-provider claude
    """
    from agora_code.scanner import scan as do_scan
    from agora_code.workflows import detect_workflows, generate_flow_code

    _echo(f"🔍 Scanning {target!r}...")
    catalog = asyncio.run(do_scan(target))

    if len(catalog) == 0:
        _echo("⚠️  No routes found — try --use-llm or point at an OpenAPI URL.")
        return

    _echo(f"✅ {len(catalog)} routes found via {catalog.extractor} extractor")
    _echo(f"🤖 Detecting workflows with LLM ({llm_provider})...\n")

    try:
        workflow_catalog = asyncio.run(
            detect_workflows(catalog, provider=llm_provider, model=llm_model)
        )
    except RuntimeError as e:
        _echo(f"❌ {e}")
        _echo("\nTo detect workflows, set one of:")
        _echo("  ANTHROPIC_API_KEY  (Claude — recommended)")
        _echo("  OPENAI_API_KEY     (GPT-4o-mini)")
        _echo("  GEMINI_API_KEY     (Gemini Flash)")
        return

    if len(workflow_catalog) == 0:
        _echo("📭 No multi-step workflows detected.")
        _echo("   This may mean the routes are all independent endpoints.")
        return

    _echo(f"✅ {len(workflow_catalog)} workflow(s) detected:\n")

    for wf in workflow_catalog.workflows:
        _echo(f"  ◆  {wf.name}")
        _echo(f"     {wf.description}")
        _echo(f"     Steps: {' → '.join(f'{s.route_method} {s.route_path}' for s in wf.steps)}")
        if wf.trigger_keywords:
            _echo(f"     Triggers: {', '.join(wf.trigger_keywords)}")
        _echo("")

    # Show Mermaid DAG
    if show_mermaid:
        _echo("─" * 60)
        _echo("📊 DAG (Mermaid):\n")
        for wf in workflow_catalog.workflows:
            _echo(f"  # {wf.name}")
            _echo("  graph TD")
            for i, step in enumerate(wf.steps[:-1]):
                a = step.route_path.replace("/", "_").replace("{", "").replace("}", "")
                b = wf.steps[i+1].route_path.replace("/", "_").replace("{", "").replace("}", "")
                _echo(f"      {step.route_method}{a} --> {wf.steps[i+1].route_method}{b}")
            _echo("")

    # Save generated code
    if output:
        import os
        os.makedirs(output, exist_ok=True)

        # Save workflow catalog JSON
        json_path = os.path.join(output, "workflows.json")
        with open(json_path, "w") as f:
            f.write(workflow_catalog.to_json())
        _echo(f"💾 Workflow catalog saved to {json_path}")

        # Save individual flow Python files
        for wf in workflow_catalog.workflows:
            code = generate_flow_code(wf, base_url="http://localhost:8000")
            code_path = os.path.join(output, f"{wf.name}.py")
            with open(code_path, "w") as f:
                f.write(code)
            _echo(f"   Generated: {code_path}")

        _echo(f"\n✅ Edit the generated files, then run: python {wf.name}.py")
    else:
        _echo("💡 Add --output ./workflows to save generated Python flow files.")



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
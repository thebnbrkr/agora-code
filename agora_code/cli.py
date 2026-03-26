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
    from agora_code.log import configure
    configure()


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
@click.option("--cache", is_flag=True, default=False,
              help="Use cached discovered_routes.json if present (skip re-scanning)")
@click.option("--quiet", is_flag=True, default=False,
              help="Suppress output — for hook/automation use")
def scan(target, output, use_llm, llm_provider, fmt, enterprise, cache, quiet):
    """Scan a codebase or URL and discover all API routes.

    TARGET can be a local directory path or a remote URL:

    \b
    agora-code scan ./my-fastapi-app
    agora-code scan https://api.example.com
    agora-code scan ./my-app --output routes.json
    agora-code scan ./node-app --use-llm
    agora-code scan . --cache --quiet        # hook-safe: uses cache, no output
    """
    from agora_code.scanner import scan as do_scan

    # --cache: load discovered_routes.json from cwd if it exists
    if cache:
        cache_file = Path("discovered_routes.json")
        if cache_file.exists():
            from agora_code.models import RouteCatalog
            try:
                catalog = RouteCatalog.from_json(cache_file.read_text(encoding="utf-8"))
                if not quiet:
                    _echo(f"✅ Loaded {len(catalog)} cached routes from {cache_file}")
                if output:
                    Path(output).write_text(catalog.to_json(), encoding="utf-8")
                return
            except Exception as e:
                if not quiet:
                    _echo(f"⚠️  Cache file unreadable ({e}), falling back to live scan", err=True)

    if not quiet:
        _echo(f"🔍 Scanning {target!r}...")

    catalog = asyncio.run(do_scan(
        target,
        use_llm=use_llm,
        llm_provider=llm_provider,
        edition="enterprise" if enterprise else "community",
    ))

    if len(catalog) == 0:
        if not quiet:
            _echo("⚠️  No routes found. Try --use-llm for non-Python/non-OpenAPI repos.")
        return

    if not quiet:
        _echo(f"✅ Found {len(catalog)} routes via {catalog.extractor} extractor\n")

    if not quiet:
        if fmt == "json":
            click.echo(catalog.to_json())
        elif fmt == "mcp":
            click.echo(json.dumps(catalog.to_mcp_tools(), indent=2))
        else:
            _print_routes_table(catalog)

    if output:
        Path(output).write_text(catalog.to_json(), encoding="utf-8")
        if not quiet:
            _echo(f"\n💾 Saved to {output}")

    if not quiet:
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
@click.option("--enterprise", is_flag=True, default=False)
def serve(target, url, use_llm, llm_provider, auth_token, auth_type, enterprise):
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

    server = MCPServer(
        catalog=catalog,
        base_url=url,
        auth=auth,
        edition="enterprise" if enterprise else "community",
    )

    from agora_code.vector_store import get_store
    db = get_store()
    _echo(f"🚀 MCP server ready ({len(catalog)} tools) — memory: {db.db_path}", err=True)
    asyncio.run(server.serve())


# --------------------------------------------------------------------------- #
#  stats                                                                       #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("target")
@click.option("--window", default=24, help="Time window in hours for pattern detection")
def stats(target, window):
    """Show API call stats and patterns from memory.

    \b
    agora-code stats ./my-api
    agora-code stats ./my-api --window 48
    """
    from agora_code.vector_store import get_store

    async def _run():
        from agora_code.scanner import scan as do_scan
        catalog = await do_scan(target)
        store = get_store()

        _echo(f"\n📊 API Stats — {target}  (DB: {store.db_path})\n")

        any_calls = False
        for route in catalog.routes[:20]:
            s = store.get_endpoint_stats(route.method, route.path)
            if s["total"] == 0:
                continue
            any_calls = True
            success_pct = int(s["success_rate"] * 100) if s["success_rate"] else 0
            latency = f"{s['avg_latency_ms']:.0f}ms" if s["avg_latency_ms"] else "—"
            _echo(
                f"  {route.method:6} {route.path:40} "
                f"{s['total']:4} calls  "
                f"{success_pct:3}% ok  "
                f"{latency}"
            )

        if not any_calls:
            _echo("  No API calls logged yet. Run agora-code serve to start tracking.")

        _echo("\n🔍 Failure patterns:\n")
        for route in catalog.routes[:20]:
            patterns = store.get_failure_patterns(route.path)
            for p in patterns:
                _echo(f"  ⚠️  {route.path}: {p['occurrences']} failures with params {p['params']}")

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
    from agora_code.compress import compress_catalog

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
@click.option("--project", "-p", is_flag=True, default=False,
              help="Scope counts to the current repo only")
def status(project):
    """Show current session state and recent call stats.

    \b
    agora-code status           # global counts
    agora-code status --project # scoped to this repo
    agora-code status -p
    """
    from agora_code.session import load_session, _get_project_id
    from agora_code.compress import compress_session, _session_age_str
    from agora_code.vector_store import get_store

    session = load_session()
    if not session:
        _echo("No active session. Start one with:")
        _echo("   agora-code checkpoint --goal \"What you're trying to do\"")
    else:
        age = _session_age_str(session)
        started = session.get("started_at", "")[:19].replace("T", " ")
        last = session.get("last_active", "")[:19].replace("T", " ")
        _echo(f"session: {session.get('session_id', 'unknown')}")
        _echo(f"  started:     {started} UTC")
        _echo(f"  last active: {last} UTC  ({age})")
        _echo(compress_session(session, level="detail"))

    store = get_store()
    conn = store._conn_()
    if project:
        pid = _get_project_id()
        if not pid:
            _echo("No project_id (not in a git repo).")
            return
        sessions   = conn.execute("SELECT COUNT(*) FROM sessions WHERE project_id=?", (pid,)).fetchone()[0]
        learnings  = conn.execute("SELECT COUNT(*) FROM learnings WHERE project_id=?", (pid,)).fetchone()[0]
        snapshots  = conn.execute("SELECT COUNT(*) FROM file_snapshots WHERE project_id=?", (pid,)).fetchone()[0]
        symbols    = conn.execute("SELECT COUNT(*) FROM symbol_notes WHERE project_id=?", (pid,)).fetchone()[0]
        _echo(f"\nproject: {pid}")
        _echo(f"  {sessions} sessions  {learnings} learnings  {snapshots} file snapshots  {symbols} symbols")
    else:
        stats = store.get_stats()
        _echo(f"\nmemory (global): {stats['sessions']} sessions  {stats['learnings']} learnings  "
              f"{stats['api_calls']} API calls  [DB: {stats['db_path']}]"
              f"  [vector: {'on' if stats['vector_search'] else 'off'}]")


# --------------------------------------------------------------------------- #
#  memory                                                                     #
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--limit", "-n", default=10, help="Max sessions and learnings to list (default 10)")
@click.option("--verbose", "-v", is_flag=True, help="Show stored AST summaries and code blocks from the DB")
@click.argument("limit_arg", required=False, type=int)
def memory(limit, limit_arg, verbose):
    """Show DB path, counts, and a short dump of recent sessions and learnings.

    Each time a file is read and indexed (on-read / on-edit hooks), we store:
    - The full AST summary in file_snapshots.summary
    - The full code block (source lines) per function/class in symbol_notes.code_block
    Use --verbose to print a sample of that stored content.

    For full inspection: sqlite3 <path from status or memory>.

    Examples:
      agora-code memory
      agora-code memory 20
      agora-code memory --limit 20 --verbose
    """
    if limit_arg is not None:
        limit = limit_arg
    from agora_code.vector_store import get_store

    store = get_store()
    stats = store.get_stats()
    _echo(f"DB path: {stats['db_path']}")
    _echo(f"Counts:  {stats['sessions']} sessions, {stats['learnings']} learnings, "
          f"{stats['api_calls']} API calls, {stats.get('file_snapshots', 0)} file snapshots (AST), "
          f"{stats.get('symbol_notes', 0)} symbol notes  [vector: {'on' if stats['vector_search'] else 'off'}]")
    _echo("")

    sessions = store.list_sessions(limit=limit)
    if sessions:
        _echo(f"Recent sessions (last {len(sessions)}):")
        for s in sessions:
            status_icon = {"in_progress": "🔄", "complete": "✅", "abandoned": "❌"}.get(
                s.get("status", ""), "📋"
            )
            goal_str = s.get("goal") or ""
            goal = goal_str[:50] + ("..." if len(goal_str) > 50 else "")
            _echo(f"  {status_icon} {s.get('session_id', '')[:44]}  {s.get('last_active', '')[:10]}  {goal}")
        _echo("")
    else:
        _echo("No sessions in DB yet.")
        _echo("")

    learnings = store.search_learnings_keyword("", k=limit)
    if learnings:
        _echo(f"Recent learnings (last {len(learnings)}):")
        for L in learnings:
            finding_str = L.get("finding") or ""
            finding = finding_str[:60] + ("..." if len(finding_str) > 60 else "")
            _echo(f"  · [{L.get('type', 'finding')}] {finding}")
        _echo("")
    else:
        _echo("No learnings in DB yet.")
        _echo("")

    # Indexed files (AST summaries from read/edit hooks)
    snapshots = store.search_file_snapshots("", k=limit)
    if snapshots:
        _echo(f"Indexed files (AST snapshots, last {len(snapshots)}):")
        for snp in snapshots:
            fp = snp.get("file_path", "")
            ts = (snp.get("timestamp") or "")[:10]
            summary = (snp.get("summary") or "").strip()
            symbols_col = snp.get("symbols") or ""
            n_symbols = len(symbols_col.split("\n")) if symbols_col else 0
            try:
                import json
                names = json.loads(symbols_col) if symbols_col.strip().startswith("[") else []
                n_symbols = len(names) if isinstance(names, list) else n_symbols
            except Exception:
                pass
            _echo(f"  📄 {fp}  [{ts}]  {n_symbols} symbols")
            if summary:
                preview = summary[:80] + ("..." if len(summary) > 80 else "")
                _echo(f"      {preview}")
            if verbose and summary:
                # Show stored AST summary (first 400 chars)
                excerpt = summary[:400] + ("\n      ... [truncated]" if len(summary) > 400 else "")
                for line in excerpt.splitlines():
                    _echo(f"      | {line}")
        _echo("")
    else:
        _echo("No file snapshots (AST) in DB yet. Read/edit hooks populate these.")
        _echo("")

    # Symbol index (functions/classes from AST) — each has code_block stored
    symbols = store.search_symbol_notes("", k=min(limit * 3, 50))
    if symbols:
        _echo(f"Symbol index (functions/classes, sample {len(symbols)}):")
        for sym in symbols:
            fp = sym.get("file_path", "")
            name = sym.get("symbol_name", "")
            stype = sym.get("symbol_type", "?")
            line = sym.get("start_line") or "?"
            sig = (sym.get("signature") or "").strip()[:50]
            if len((sym.get("signature") or "")) > 50:
                sig += "..."
            _echo(f"  {stype}: {name} @ {fp}:{line}  {sig}")
        _echo("")
        if verbose:
            syms_with_blocks = store.list_recent_symbol_notes_with_blocks(limit=5)
            if syms_with_blocks:
                _echo("Stored code blocks (sample, last 5 by timestamp):")
                for sym in syms_with_blocks:
                    block = (sym.get("code_block") or "").strip()
                    if not block:
                        continue
                    _echo(f"  --- {sym.get('symbol_type')} {sym.get('symbol_name')} @ {sym.get('file_path')}:{sym.get('start_line')} ---")
                    lines = block.splitlines()[:25]
                    for ln in lines:
                        _echo(f"  | {ln}")
                    if block.count("\n") >= 25:
                        _echo("  | ... [truncated]")
                    _echo("")
    else:
        _echo("No symbol notes in DB yet. Read/edit hooks populate these.")
        _echo("")

    _echo("For full inspection: sqlite3 " + stats["db_path"])


# --------------------------------------------------------------------------- #
#  list-* — see every DB table without SQL                                     #
# --------------------------------------------------------------------------- #

@main.command("list-sessions")
@click.option("--limit", "-n", default=20, help="Max sessions to show")
def list_sessions(limit):
    """List sessions in the DB (no SQL). Same data as memory, sessions section."""
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id
    store = get_store()
    pid = _get_project_id()
    sessions = store.list_sessions(limit=limit, project_id=pid)
    if not sessions:
        _echo("No sessions in DB. Use checkpoint / complete to create some.")
        return
    _echo(f"Sessions (last {len(sessions)}):")
    for s in sessions:
        status_icon = {"in_progress": "🔄", "complete": "✅", "abandoned": "❌"}.get(s.get("status", ""), "📋")
        goal = (s.get("goal") or "")[:60]
        _echo(f"  {status_icon} {s.get('session_id', '')[:44]}  {s.get('last_active', '')[:10]}  {goal}")


@main.command("list-learnings")
@click.option("--limit", "-n", default=20, help="Max learnings to show")
def list_learnings(limit):
    """List recent learnings in the DB (no SQL)."""
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id
    store = get_store()
    learnings = store.search_learnings_keyword("", k=limit, project_id=_get_project_id())
    if not learnings:
        _echo("No learnings in DB. Use learn or let on-stop extract from transcripts.")
        return
    _echo(f"Learnings (last {len(learnings)}):")
    for L in learnings:
        finding = (L.get("finding") or "")[:80]
        _echo(f"  [{L.get('type', 'finding')}] {finding}")


@main.command("list-snapshots")
@click.option("--limit", "-n", default=20, help="Max file snapshots to show")
def list_snapshots(limit):
    """List file_snapshots (AST summaries) in the DB (no SQL)."""
    from agora_code.vector_store import get_store
    store = get_store()
    snapshots = store.search_file_snapshots("", k=limit)
    if not snapshots:
        _echo("No file snapshots. Read/edit hooks populate these when you open or edit files.")
        return
    _echo(f"File snapshots (last {len(snapshots)}):")
    for s in snapshots:
        _echo(f"  📄 {s.get('file_path', '')}  {s.get('timestamp', '')[:10]}")


@main.command("list-symbols")
@click.option("--limit", "-n", default=30, help="Max symbol notes to show")
@click.option("--file", "file_path", default=None, help="Filter by file path")
def list_symbols(limit, file_path):
    """List symbol_notes (functions/classes) in the DB (no SQL)."""
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id, _get_git_branch
    store = get_store()
    if file_path:
        syms = store.get_symbols_for_file(file_path, project_id=_get_project_id(), branch=_get_git_branch())
        syms = syms[:limit] if syms else []
    else:
        syms = store.search_symbol_notes("", k=limit)
    if not syms:
        _echo("No symbol notes. Read/edit hooks populate these when you open or edit code files.")
        return
    _echo(f"Symbol notes ({len(syms)}):")
    for s in syms:
        _echo(f"  {s.get('symbol_type', '?')}: {s.get('symbol_name', '')} @ {s.get('file_path', '')}:{s.get('start_line', '?')}")


@main.command("list-file-changes")
@click.option("--limit", "-n", default=20, help="Max file changes to show")
def list_file_changes(limit):
    """List recent file_changes in the DB (no SQL). Per-file history: file-history <path>."""
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id
    store = get_store()
    pid = _get_project_id()
    if not pid:
        _echo("No project_id (e.g. not in a git repo). file-history <path> still works.")
        return
    changes = store.get_recent_file_changes_for_project(pid, limit=limit)
    if not changes:
        _echo("No file changes in DB. Edits + track-diff populate these.")
        return
    _echo(f"File changes (last {len(changes)}):")
    for c in changes:
        st = c.get("status") or "uncommitted"
        sha = c.get("commit_sha") or c.get("recorded_at_commit_sha") or ""
        sha_str = f"  [{st}]" + (f" {sha[:8]}" if sha else "")
        _echo(f"  {c.get('file_path', '')}  {c.get('timestamp', '')[:10]}{sha_str}  {(c.get('diff_summary') or '')[:50]}")


@main.command("list-api-calls")
@click.option("--limit", "-n", default=20, help="Max API calls to show")
def list_api_calls(limit):
    """List recent api_calls in the DB (no SQL). From serve/chat when calling an API."""
    from agora_code.vector_store import get_store
    store = get_store()
    calls = store.list_recent_api_calls(limit=limit)
    if not calls:
        _echo("No API calls in DB. Use agora-code serve/chat to log calls.")
        return
    _echo(f"API calls (last {len(calls)}):")
    for c in calls:
        _echo(f"  {c.get('method', '')} {c.get('path', '')}  {c.get('response_status')}  {c.get('latency_ms')}ms")


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
@click.option("--quiet", is_flag=True, default=False,
              help="Suppress output — for hook/automation use")
def checkpoint(goal, hypothesis, action, context, api, next_step, blocker, file_changed, quiet):
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
    if not quiet:
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
@click.option("--level", default=None,
              type=click.Choice(["index", "summary", "detail", "full"]),
              help="Compression level — auto-picks under --token-budget if not set")
@click.option("--token-budget", default=2000, help="Max tokens for auto-level picking")
@click.option("--raw", is_flag=True, default=False, help="Print raw session JSON")
@click.option("--quiet", is_flag=True, default=False,
              help="Exit silently if no session exists (for hook use)")
def inject(level, token_budget, raw, quiet):
    """Print compressed session context for injection into any coding agent.

    \b
    Use with Claude Code hooks (.claude/settings.json):
        {"hooks": {"PreToolUse": [{"command": "agora-code inject"}]}}

    Or pipe directly:
        agora-code inject | pbcopy   # paste into any chat
        agora-code inject --level detail
        agora-code inject --raw      # full session JSON
    """
    from agora_code.session import (
        load_session_if_recent, load_session,
        _build_recalled_context,
    )

    if raw:
        session = load_session_if_recent(max_age_hours=48) or load_session()
        if session:
            import json as _json
            click.echo(_json.dumps(session, indent=2))
        return

    # Always build fresh — never serve stale cache from session.json
    recalled = _build_recalled_context()
    if recalled:
        click.echo(recalled)
    elif not quiet:
        click.echo("No session context found.", err=True)



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
    from agora_code.compress import compress_session

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
    from agora_code.session import load_session, _get_project_id, _get_git_branch

    session = load_session()
    session_id = session.get("session_id") if session else None
    # Important: learnings must be stored with the current repo's project_id.
    # `agora-code inject` scopes by project_id when building the context.
    project_id = _get_project_id()
    branch = _get_git_branch()

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
        project_id=project_id,
        branch=branch,
    )
    _echo(f"✅ Learning stored (id: {lid[:8]}…)")
    if embed is None:
        _echo("   ⚠️  No embedding generated — set OPENAI_API_KEY for semantic recall.")
        _echo("   Keyword search will still work.")


# --------------------------------------------------------------------------- #
#  remove                                                                      #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("learning_id")
def remove(learning_id):
    """Remove a learning by ID — scoped to the current repo.

    \b
    agora-code remove abc12345
    """
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id

    project_id = _get_project_id()
    vs = get_store()
    conn = vs._conn_()

    row = conn.execute(
        "SELECT id, finding, project_id FROM learnings WHERE id LIKE ?",
        (f"{learning_id}%",)
    ).fetchone()

    if not row:
        _echo(f"❌ No learning found matching '{learning_id}'.")
        return

    if row["project_id"] != project_id:
        _echo(f"❌ Learning '{row['id'][:8]}' belongs to a different repo ({row['project_id']}) — cannot remove.")
        return

    conn.execute("DELETE FROM learnings WHERE id = ?", (row["id"],))
    conn.commit()
    _echo(f"✅ Removed learning: {row['finding'][:80]}")


# --------------------------------------------------------------------------- #
#  recall                                                                      #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("query", required=False, default=None)
@click.option("--limit", "-n", default=5, help="Max results")
def recall(query, limit):
    """Search your learnings knowledge base semantically.

    \b
    agora-code recall "email validation"
    agora-code recall "rate limit" --limit 10
    agora-code recall                        # show most recent learnings
    """
    from agora_code.vector_store import get_store
    from agora_code.embeddings import get_query_embedding
    from agora_code.session import _get_project_id

    vs = get_store()
    project_id = _get_project_id()

    if not query:
        # No query — show most recent learnings
        results = vs.search_learnings_keyword("", k=limit, project_id=project_id)
        mode = "recent"
    else:
        embed = get_query_embedding(query)
        if embed:
            results = vs.search_learnings_semantic(embed, k=limit, project_id=project_id)
            mode = "semantic"
        else:
            results = []
            mode = None

        if not results:
            results = vs.search_learnings_keyword(query, k=limit, project_id=project_id)
            mode = "keyword"

    if not results:
        if query:
            _echo(f"📭 No learnings match '{query}'.")
        else:
            _echo("📭 No learnings stored yet.")
        _echo("   Store one with: agora-code learn \"your finding\"")
        return

    label = "most recent" if mode == "recent" else f"{mode} search"
    _echo(f"\n🔍 {len(results)} result(s) [{label}]:\n")
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
#  index                                                                       #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("file_path", type=click.Path(exists=True))
def index(file_path):
    """Re-index a file into the DB (symbol_notes + file_snapshots). Call after edits so the AST cache stays in sync.

    Hooks (e.g. on-edit, after-file-edit) should call this so each change updates the DB.
    """
    from agora_code.indexer import index_file
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    path = Path(file_path).resolve()
    count = index_file(
        str(path),
        project_id=_get_project_id(),
        branch=_get_git_branch(),
        commit_sha=_get_commit_sha(),
    )
    if count:
        _echo(f"✅ Indexed {path.name}: {count} symbols, AST snapshot updated.")
    else:
        _echo(f"📄 {path.name}: not a code file or no symbols extracted (no DB update).")


# --------------------------------------------------------------------------- #
#  track-diff                                                                  #
# --------------------------------------------------------------------------- #

@main.command("track-diff")
@click.argument("file_path", required=False)
@click.option("--all", "all_files", is_flag=True, default=False,
              help="Track all uncommitted (staged + unstaged) files")
@click.option("--committed", is_flag=True, default=False,
              help="Diff against HEAD~1 (last commit) rather than working tree")
@click.option("--note", default=None,
              help="One sentence describing what changed and why — written by the agent")
def track_diff(file_path, all_files, committed, note):
    """Capture a git diff for a file and store a compact summary in memory.

    Pass --note with a sentence you write describing what changed and why.
    This is more accurate than auto-generated notes.

    \b
    agora-code track-diff agora_code/auth.py --note "changed _check_expiry to use utcnow — fixes tz offset, called by authenticate()"
    agora-code track-diff --all
    agora-code track-diff agora_code/auth.py --committed
    """
    import subprocess as sp
    from agora_code.session import _get_uncommitted_files

    if all_files:
        files = _get_uncommitted_files()
        if not files:
            _echo("No uncommitted files to track.")
            return
        for fp in files:
            _track_diff_one(fp, committed, note=note)
        return
    if not file_path:
        _echo("Error: Missing argument FILE_PATH (or use --all for all uncommitted files).", err=True)
        raise SystemExit(2)
    _track_diff_one(file_path, committed, note=note)


def _track_diff_one(file_path: str, committed: bool, note: Optional[str] = None) -> None:
    """Run track-diff for a single file."""
    import subprocess as sp
    from agora_code.vector_store import get_store
    from agora_code.session import load_session, _get_git_branch, _get_commit_sha, _get_git_author, _get_project_id

    if committed:
        cmd = ["git", "diff", "HEAD~1", "--", file_path]
    else:
        cmd = ["git", "diff", "HEAD", "--", file_path]

    try:
        result = sp.run(cmd, capture_output=True, text=True, timeout=10)
        raw_diff = result.stdout.strip()
    except Exception as e:
        _echo(f"⚠️  Could not get diff for {file_path}: {e}")
        return

    if not raw_diff:
        try:
            r2 = sp.run(["git", "status", "--short", "--", file_path],
                        capture_output=True, text=True, timeout=5)
            status = r2.stdout.strip()
            if "??" in status:
                raw_diff = f"[new untracked file: {file_path}]"
            else:
                return
        except Exception as e:
            _echo(f"⚠️  git status failed for {file_path}: {e}", err=True)
            return

    summary = note if note else _summarize_diff(raw_diff, file_path)
    changed_lines = [l for l in raw_diff.splitlines()
                     if l.startswith(('+', '-')) and not l.startswith(('+++', '---'))]
    snippet = '\n'.join(changed_lines)
    session = load_session()
    store = get_store()
    store.save_file_change(
        file_path=file_path,
        diff_summary=summary,
        diff_snippet=snippet,
        commit_sha=_get_commit_sha(),
        session_id=session.get("session_id") if session else None,
        branch=_get_git_branch(),
        agent_id=_get_git_author(),
        project_id=_get_project_id(),
    )
    _echo(f"📌 Tracked: {file_path} — {summary}")



def _llm_change_note(diff: str, file_path: str, symbols: str = "") -> Optional[str]:
    """
    Generate a 1-2 sentence change note using the configured LLM provider.
    Uses LLM_PROVIDER / ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY (auto-detect).
    Returns None if no provider available — caller falls back to regex.
    """
    try:
        from agora_code.extractors.llm import _detect_provider
        provider, model = _detect_provider()
        if not provider:
            return None

        symbol_hint = f"\nKnown symbols in this file: {symbols}" if symbols else ""
        prompt = (
            f"You are summarizing a code change for a developer memory system.\n"
            f"File: {file_path}{symbol_hint}\n\n"
            f"Diff:\n{diff[:3000]}\n\n"
            f"Write exactly 1-2 sentences: what changed, why (if inferrable), "
            f"and what it connects to (callers/callees if visible). "
            f"Format: 'changed <symbol> to <what> [— connects to <other>]'. "
            f"Be specific. No preamble."
        )

        import asyncio
        if provider in ("claude", "anthropic"):
            import anthropic
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=model, max_tokens=120,
                messages=[{"role": "user", "content": prompt}],
            )
            note = resp.content[0].text.strip() if resp.content else ""
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model, max_tokens=120, temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
            note = resp.choices[0].message.content.strip()
        elif provider == "gemini":
            import google.generativeai as genai
            m = genai.GenerativeModel(model)
            resp = m.generate_content(prompt)
            note = resp.text.strip() if resp.text else ""
        else:
            return None
        return note if note else None
    except Exception:
        return None


def _summarize_diff(diff: str, file_path: str) -> str:
    """
    Content-aware diff summarizer.
    Tries LLM-generated note first; falls back to regex if unavailable.
    """
    # Try LLM first — pull symbol context from DB if available
    try:
        from agora_code.vector_store import get_store
        from agora_code.session import _get_project_id, _get_git_branch
        store = get_store()
        snaps = store.search_file_snapshots(file_path, k=1)
        symbols = snaps[0].get("symbols", "") if snaps else ""
        llm_note = _llm_change_note(diff, file_path, symbols=symbols)
        if llm_note:
            import re as _re
            scale = f"+{len([l for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++')])}" \
                    f"/-{len([l for l in diff.splitlines() if l.startswith('-') and not l.startswith('---')])}"
            return f"{llm_note} ({scale})"
    except Exception:
        pass

    import re
    lines = diff.splitlines()
    added   = [l[1:].strip() for l in lines if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:].strip() for l in lines if l.startswith("-") and not l.startswith("---")]

    if not added and not removed:
        return f"{file_path}: no changes detected"

    # --- What functions/classes were touched ---
    fn_re = re.compile(r"(?:def |class |async def )(\w+)")
    added_fns, removed_fns = [], []
    for line in added:
        for m in fn_re.finditer(line):
            name = m.group(1)
            if name not in added_fns:
                added_fns.append(name)
    for line in removed:
        for m in fn_re.finditer(line):
            name = m.group(1)
            if name not in removed_fns:
                removed_fns.append(name)

    # --- What imports changed ---
    new_imports = [l for l in added if l.startswith(("import ", "from "))]
    del_imports = [l for l in removed if l.startswith(("import ", "from "))]

    # --- Meaningful added snippets (non-blank, non-comment, non-decorator) ---
    meaningful_added = [
        l for l in added
        if l and not l.startswith("#") and not l.startswith("@")
        and not l.startswith(("import ", "from ", "class ", "def ", "async def "))
    ]
    meaningful_removed = [
        l for l in removed
        if l and not l.startswith("#") and not l.startswith("@")
        and not l.startswith(("import ", "from ", "class ", "def ", "async def "))
    ]

    # --- Build description ---
    parts = []

    # New/modified functions
    new_fns = [f for f in added_fns if f not in removed_fns]
    mod_fns = [f for f in added_fns if f in removed_fns]
    del_fns = [f for f in removed_fns if f not in added_fns]

    if new_fns:
        parts.append(f"added {', '.join(new_fns[:3])}()")
    if mod_fns:
        parts.append(f"modified {', '.join(mod_fns[:3])}()")
    if del_fns:
        parts.append(f"removed {', '.join(del_fns[:2])}()")

    # Import changes
    if new_imports:
        import_names = [i.split()[-1] for i in new_imports[:2]]
        parts.append(f"imported {', '.join(import_names)}")
    if del_imports:
        import_names = [i.split()[-1] for i in del_imports[:2]]
        parts.append(f"removed import {', '.join(import_names)}")

    # Fallback: show a snippet of the most significant added line
    if not parts and meaningful_added:
        snippet = meaningful_added[0][:80].rstrip()
        parts.append(f"added: `{snippet}`")
    elif not parts and meaningful_removed:
        snippet = meaningful_removed[0][:80].rstrip()
        parts.append(f"removed: `{snippet}`")

    scale = f"+{len(added)}/-{len(removed)} lines"
    desc = "; ".join(parts) if parts else "modified"
    return f"{file_path}: {desc} ({scale})"


# --------------------------------------------------------------------------- #
#  file-history                                                                #
# --------------------------------------------------------------------------- #

@main.command("file-history")
@click.argument("file_path")
@click.option("--limit", "-n", default=20, help="Max entries to show")
def file_history(file_path, limit):
    """Show the tracked change history for a file.

    \b
    agora-code file-history agora_code/auth.py
    agora-code file-history agora_code/session.py --limit 5
    """
    from agora_code.vector_store import get_store

    history = get_store().get_file_history(file_path, limit=limit)
    if not history:
        _echo(f"📭 No tracked changes for '{file_path}'.")
        _echo("   Changes are tracked automatically via git post-commit hook.")
        _echo("   Install with: agora-code install-hooks")
        _echo(f"   Or run manually: agora-code track-diff {file_path}")
        return

    _echo(f"\n📋 Change history for {file_path} ({len(history)} entries):\n")
    for entry in history:
        ts = entry.get("timestamp", "")[:16]
        branch = f" [{entry['branch']}]" if entry.get("branch") else ""
        sha = f" @{entry['commit_sha'][:8]}" if entry.get("commit_sha") else ""
        author = f" by {entry['author']}" if entry.get("author") else ""
        session = f" (session: {entry['session_id'][:20]}...)" if entry.get("session_id") else ""
        _echo(f"  {ts}{branch}{sha}{author}")
        _echo(f"    {entry.get('diff_summary', '(no summary)')}{session}")
    _echo("")


# --------------------------------------------------------------------------- #
#  learn-from-commit                                                           #
# --------------------------------------------------------------------------- #

@main.command("learn-from-commit")
@click.argument("sha", required=False, default=None)
@click.option("--quiet", "-q", is_flag=True, default=False)
def learn_from_commit(sha, quiet):
    """Derive and store learnings from a git commit (defaults to HEAD).

    Called automatically by on-bash.sh after every git commit.
    Uses LLM to extract structural facts and design decisions.
    Falls back to storing commit message as a raw learning if no LLM key.

    \b
    agora-code learn-from-commit           # HEAD
    agora-code learn-from-commit abc1234   # specific commit
    """
    import subprocess as sp
    import json as _json
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id, _get_git_branch, load_session

    # Resolve SHA
    if not sha:
        r = sp.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        sha = r.stdout.strip()
    if not sha:
        _echo("⚠  Could not determine commit SHA.", err=True)
        return

    # Commit message
    r = sp.run(["git", "log", "--format=%B", "-1", sha], capture_output=True, text=True)
    commit_message = r.stdout.strip()
    if not commit_message:
        if not quiet:
            _echo(f"⚠  No commit message found for {sha}.")
        return

    # Files changed in this commit
    r = sp.run(["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
               capture_output=True, text=True)
    files = [f for f in r.stdout.strip().splitlines() if f.strip()]
    if not files:
        r = sp.run(["git", "show", "--name-only", "--format=", sha],
                   capture_output=True, text=True)
        files = [f for f in r.stdout.strip().splitlines() if f.strip()]

    store = get_store()
    project_id = _get_project_id()
    branch = _get_git_branch()
    session = load_session()
    session_id = session.get("session_id") if session else None

    # Get ALL change notes for each committed file — every attempt, not just the last one.
    import re as _re
    stored = 0
    for fp in files:
        rows = store.get_file_changes_for_commit(fp, sha, project_id=project_id)
        if not rows:
            # fallback: most recent note for this file regardless of SHA
            history = store.get_file_history(fp, limit=1)
            rows = history if history else []

        for row in rows:
            note = (row.get("diff_summary") or "").strip()
            # Strip any leading "filepath: " prefix stored by _summarize_diff
            clean = _re.sub(r'^[^\s:]+[/\\][^\s:]*:\s*', '', note)
            if not clean or (clean.startswith("modified ") and len(clean) < 20):
                continue
            finding = f"{clean}  [{fp.split('/')[-1]}]"
            store.store_learning(
                finding=finding,
                evidence=f"commit {sha}: {commit_message[:80]}",
                confidence="confirmed",
                tags=["commit", "change-note"],
                type="finding",
                branch=branch,
                files=[fp],
                project_id=project_id,
                session_id=session_id,
                commit_sha=sha,
            )
            stored += 1

    # If no file notes had content, store the commit message as a minimal signal
    if stored == 0:
        store.store_learning(
            finding=commit_message.splitlines()[0][:120],
            evidence=f"commit {sha} — no change notes available",
            confidence="likely",
            tags=["commit"],
            type="finding",
            branch=branch,
            files=files,
            project_id=project_id,
            session_id=session_id,
            commit_sha=sha,
        )
        stored = 1

    if not quiet:
        _echo(f"✅ {stored} learning(s) stored for commit {sha}: {commit_message.splitlines()[0][:60]}")


# --------------------------------------------------------------------------- #
#  show  — pretty view of what inject loaded                                   #
# --------------------------------------------------------------------------- #

@main.command("show")
@click.option("--json-out", "json_out", is_flag=True, default=False, help="Output as JSON")
def show(json_out):
    """Show everything currently in session context — what inject would load.

    Renders as a rich markdown table in the terminal so you can see exactly
    what the AI is working with.

    \b
    agora-code show
    agora-code show --json-out
    """
    import subprocess as sp
    import json as _json
    from agora_code.vector_store import get_store
    from agora_code.session import (
        load_session, _get_project_id, _get_git_branch,
        _get_commit_sha, _get_uncommitted_files,
    )

    store = get_store()
    project_id = _get_project_id()
    branch = _get_git_branch()
    session = load_session()
    session_data = _json.loads(session.get("session_data") or "{}") if session else {}

    # ── Recent commits on branch ──────────────────────────────────────────────
    r = sp.run(
        ["git", "log", "--format=%h|%s|%ai", "-6"],
        capture_output=True, text=True,
    )
    recent_commits = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            recent_commits.append({"sha": parts[0], "msg": parts[1], "date": parts[2][:10]})

    # ── Learnings for last 3 commits on this branch ──────────────────────────
    branch_shas = [c["sha"] for c in recent_commits[:3]]
    commit_learnings = store.get_learnings_for_commits(branch_shas, project_id=project_id)

    # ── Uncommitted file changes — always read live from git ─────────────────
    try:
        import subprocess as _sp
        _u = _sp.run(["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True, timeout=5)
        _s = _sp.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, timeout=5)
        dirty_files = list(dict.fromkeys(
            [f for f in _u.stdout.strip().splitlines() if f] +
            [f for f in _s.stdout.strip().splitlines() if f]
        ))
    except Exception:
        dirty_files = []
    uncommitted_changes = store.get_uncommitted_file_changes(
        project_id=project_id, branch=branch
    ) if dirty_files else []

    # ── Session checkpoint ────────────────────────────────────────────────────
    session_goal = session.get("goal", "") if session else ""
    session_decisions = session_data.get("decisions_made", []) if session_data else []
    session_next = session_data.get("next_steps", []) if session_data else []

    if json_out:
        import json
        click.echo(json.dumps({
            "session": {
                "goal": session_goal,
                "decisions": session_decisions,
                "next_steps": session_next,
            },
            "uncommitted_changes": uncommitted_changes,
            "commit_learnings": commit_learnings,
            "recent_commits": recent_commits,
            "dirty_files": dirty_files,
        }, indent=2))
        return

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        console = Console()
        _use_rich = True
    except ImportError:
        console = None
        _use_rich = False

    def _line(s=""):
        click.echo(s)

    _line("# AGORA SESSION CONTEXT")
    _line()

    # Session checkpoint
    _line("## Last Session")
    if session_goal:
        _line(f"  goal:      {session_goal}")
    if session_decisions:
        for d in session_decisions[:3]:
            _line(f"  decided:   {d}")
    if session_next:
        for n in session_next[:2]:
            _line(f"  next:      {n}")
    if not session_goal:
        _line("  (no session checkpoint)")
    _line()

    # Uncommitted work
    if uncommitted_changes:
        _line("## Uncommitted Work")
        if _use_rich:
            t = Table(show_header=True, header_style="bold cyan")
            t.add_column("File", style="yellow")
            t.add_column("Change Note")
            for ch in uncommitted_changes[:10]:
                fp = ch.get("file_path", "")
                note = ch.get("diff_summary", "(no note)")
                t.add_row(fp.split("/")[-1], note)
            console.print(t)
        else:
            for ch in uncommitted_changes[:10]:
                _line(f"  {ch.get('file_path','')}")
                _line(f"    {ch.get('diff_summary','')}")
        _line()
    elif dirty_files:
        _line("## Uncommitted Work")
        _line("  dirty files (no change notes yet — run agora-code track-diff):")
        for f in dirty_files[:8]:
            _line(f"    {f}")
        _line()

    # Commit learnings
    if commit_learnings:
        _line(f"## Learnings (last {len(branch_shas)} commits on {branch})")
        if _use_rich:
            t = Table(show_header=True, header_style="bold cyan")
            t.add_column("Finding")
            t.add_column("Commit", width=8)
            t.add_column("Tags", width=20)
            for lrn in commit_learnings[:8]:
                tags = lrn.get("tags") or "[]"
                if isinstance(tags, str):
                    try:
                        import json
                        tags = ", ".join(json.loads(tags))
                    except Exception:
                        pass
                finding = lrn.get("finding", "")[:80]
                sha = (lrn.get("commit_sha") or "")[:7]
                t.add_row(finding, sha, str(tags)[:20])
            console.print(t)
        else:
            for lrn in commit_learnings[:8]:
                sha = (lrn.get("commit_sha") or "")[:7]
                _line(f"  [{sha}] {lrn.get('finding','')}")
        _line()

    # Git state
    _line("## Git State")
    _line(f"  branch:  {branch or '(unknown)'}")
    _line(f"  dirty:   {', '.join(dirty_files) if dirty_files else '(clean)'}")
    if recent_commits:
        _line("  recent commits:")
        for c in recent_commits[:4]:
            _line(f"    {c['sha']}  {c['date']}  {c['msg'][:60]}")
    _line()


# --------------------------------------------------------------------------- #
#  notes  — view AI-written change notes                                       #
# --------------------------------------------------------------------------- #

@main.command("notes")
@click.argument("file_path", required=False, default=None)
@click.option("--limit", "-n", default=20)
def notes(file_path, limit):
    """Show AI-written change notes for files.

    \b
    agora-code notes                     # all recent notes
    agora-code notes agora_code/auth.py  # notes for a specific file
    """
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id

    store = get_store()
    project_id = _get_project_id()

    if file_path:
        rows = store.get_file_history(file_path, limit=limit)
    else:
        rows = store.get_recent_file_changes_for_project(project_id, limit=limit)

    if not rows:
        _echo("📭 No change notes found.")
        _echo("   Notes are written automatically when files are edited.")
        return

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("File", style="yellow", max_width=30)
        t.add_column("Change Note")
        t.add_column("Commit", width=8)
        t.add_column("Date", width=10)
        for row in rows:
            fp = (row.get("file_path") or "").split("/")[-1]
            note = row.get("diff_summary") or "(no note)"
            sha = (row.get("commit_sha") or "")[:7]
            date = (row.get("timestamp") or "")[:10]
            t.add_row(fp, note, sha, date)
        console.print(t)
    except ImportError:
        for row in rows:
            ts = (row.get("timestamp") or "")[:10]
            sha = (row.get("commit_sha") or "")[:7]
            _echo(f"  [{ts}] @{sha}  {row.get('file_path','')}:")
            _echo(f"    {row.get('diff_summary','')}")


# --------------------------------------------------------------------------- #
#  commit-log  — learnings per commit                                          #
# --------------------------------------------------------------------------- #

@main.command("commit-log")
@click.argument("sha", required=False, default=None)
@click.option("--limit", "-n", default=5, help="Number of recent commits to show")
def commit_log(sha, limit):
    """Show learnings stored per commit.

    \b
    agora-code commit-log              # last N commits with their learnings
    agora-code commit-log abc1234      # specific commit
    """
    import subprocess as sp
    from agora_code.vector_store import get_store
    from agora_code.session import _get_project_id

    store = get_store()
    project_id = _get_project_id()

    if sha:
        commits = [{"sha": sha, "msg": "", "date": ""}]
    else:
        r = sp.run(["git", "log", "--format=%h|%s|%ai", f"-{limit}"],
                   capture_output=True, text=True)
        commits = []
        for line in r.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"sha": parts[0], "msg": parts[1], "date": parts[2][:10]})

    if not commits:
        _echo("📭 No commits found.")
        return

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        console = Console()
        _use_rich = True
    except ImportError:
        _use_rich = False

    for commit in commits:
        c_sha = commit["sha"]
        c_msg = commit["msg"]
        c_date = commit["date"]
        learnings = store.get_learnings_for_commit(c_sha, project_id=project_id)

        header = f"  {c_sha}  {c_date}  {c_msg[:60]}" if c_msg else f"  {c_sha}"
        click.echo(f"\n{header}")
        if learnings:
            if _use_rich:
                t = Table(show_header=False, box=None, padding=(0, 2))
                t.add_column("type", style="dim", width=10)
                t.add_column("finding")
                for lrn in learnings:
                    import json
                    tags = lrn.get("tags") or "[]"
                    try:
                        tags_str = ", ".join(json.loads(tags)) if isinstance(tags, str) else ", ".join(tags)
                    except Exception:
                        tags_str = str(tags)
                    t.add_row(
                        lrn.get("type", "finding"),
                        f"{lrn.get('finding','')}" + (f"  [{tags_str}]" if tags_str else ""),
                    )
                console.print(t)
            else:
                for lrn in learnings:
                    click.echo(f"    · {lrn.get('finding','')}")
        else:
            click.echo("    (no learnings stored — run: agora-code learn-from-commit " + c_sha + ")")


# --------------------------------------------------------------------------- #
#  install-hooks                                                               #
# --------------------------------------------------------------------------- #

@main.command("install-hooks")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing hook")
@click.option("--claude-code", "claude_code", is_flag=True, default=False,
              help="Install Claude Code hooks (.claude/hooks.json + shell scripts)")
def install_hooks(force, claude_code):
    """Install hooks to auto-track file changes.

    \b
    Git post-commit hook (default):
      agora-code install-hooks
      agora-code install-hooks --force

    Claude Code hooks:
      agora-code install-hooks --claude-code
      agora-code install-hooks --claude-code --force
    """
    if claude_code:
        _install_claude_code_hooks(force)
        return

    import stat
    git_hooks_dir = Path(".git/hooks")
    if not git_hooks_dir.is_dir():
        _echo("❌ Not a git repository (no .git/hooks found).")
        return

    hook_path = git_hooks_dir / "post-commit"
    if hook_path.exists() and not force:
        _echo(f"⚠️  {hook_path} already exists. Use --force to overwrite.")
        _echo("   You can manually append this to it:")
        _echo("   agora-code track-diff --committed --all-changed")
        return

    hook_script = """#!/bin/sh
# agora-code post-commit hook
# Captures what changed on each commit and stores summaries in memory.
# Installed by: agora-code install-hooks

CHANGED_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null)
if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

echo "$CHANGED_FILES" | while IFS= read -r file; do
    if [ -f "$file" ]; then
        agora-code track-diff "$file" --committed 2>/dev/null || true
    fi
done
"""

    hook_path.write_text(hook_script, encoding="utf-8")
    # Make executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    _echo(f"✅ Git post-commit hook installed at {hook_path}")
    _echo("   Fires on every commit — human or AI.")
    _echo("   Tracks: what changed, who committed, which branch, commit SHA.")
    _echo("   View history with: agora-code file-history <file>")


def _get_skill_md_content() -> str | None:
    """Return SKILL.md content — bundled inside the package so it works after pip install."""
    candidates = [
        Path(__file__).parent / "SKILL.md",                                    # installed package
        Path(__file__).parent.parent / ".claude" / "skills" / "agora-code" / "SKILL.md",  # dev/editable
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def _install_claude_code_hooks(force: bool) -> None:
    """Generate .claude/settings.json and shell scripts for Claude Code integration."""
    import shutil
    import stat

    agora_bin = "agora-code"

    claude_dir = Path(".claude")
    hooks_dir = claude_dir / "hooks"
    hooks_json_path = claude_dir / "settings.json"

    if hooks_json_path.exists() and not force:
        _echo("⚠️  .claude/settings.json already exists. Use --force to overwrite.")
        return

    hooks_dir.mkdir(parents=True, exist_ok=True)

    # --- settings.json ---
    hooks_json = f"""{{
    "hooks": {{
        "SessionStart": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": "{agora_bin} inject --quiet 2>/dev/null || true"
                    }}
                ]
            }}
        ],
        "UserPromptSubmit": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-prompt.sh"
                    }}
                ]
            }}
        ],
        "PreToolUse": [
            {{
                "matcher": "Read",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/pre-read.sh"
                    }}
                ]
            }}
        ],
        "PostToolUse": [
            {{
                "matcher": "Read",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-read.sh"
                    }}
                ]
            }},
            {{
                "matcher": "Write|Edit|MultiEdit",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-edit.sh"
                    }}
                ]
            }},
            {{
                "matcher": "Bash",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-bash.sh"
                    }}
                ]
            }},
            {{
                "matcher": "Grep",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-grep.sh"
                    }}
                ]
            }}
        ],
        "PostToolUseFailure": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-tool-failure.sh"
                    }}
                ]
            }}
        ],
        "SubagentStart": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-subagent.sh"
                    }}
                ]
            }}
        ],
        "PreCompact": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": "{agora_bin} checkpoint --quiet 2>/dev/null || true"
                    }}
                ]
            }}
        ],
        "PostCompact": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": "{agora_bin} inject --quiet 2>/dev/null || true"
                    }}
                ]
            }}
        ],
        "Stop": [
            {{
                "matcher": "",
                "hooks": [
                    {{
                        "type": "command",
                        "command": ".claude/hooks/on-stop.sh"
                    }}
                ]
            }}
        ]
    }}
}}
"""

    # --- on-prompt.sh: auto-set goal + recall relevant learnings on each prompt ---
    on_prompt = f"""#!/bin/sh
INPUT=$(cat)

PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$PROMPT" ]; then exit 0; fi

# Auto-set goal from first substantive prompt if no goal exists yet
CURRENT_GOAL=$({agora_bin} inject --quiet 2>/dev/null)
if [ -z "$CURRENT_GOAL" ]; then
    IS_SUBSTANTIVE=$(printf '%s' "$PROMPT" | python3 -c "
import sys, re
text = sys.stdin.read().strip()
if len(text) < 30:
    print('no')
elif re.match(r'^(hi|hey|hello|ok|okay|yes|no|sure|thanks|bye|lol)\\\\b', text, re.I):
    print('no')
elif re.match(r'^agora-code\\\\s', text):
    print('no')
else:
    print('yes')
" 2>/dev/null)
    if [ "$IS_SUBSTANTIVE" = "yes" ]; then
        SHORT_GOAL=$(printf '%s' "$PROMPT" | cut -c1-120)
        {agora_bin} checkpoint --goal "$SHORT_GOAL" --quiet 2>/dev/null || true
    fi
fi

# Recall relevant learnings for this prompt
LEARNINGS=$({agora_bin} recall "$PROMPT" --limit 2 2>/dev/null)
if [ -n "$LEARNINGS" ] && ! echo "$LEARNINGS" | grep -q "No learnings match"; then
    printf '[agora-code: relevant learnings for this prompt]\\n%s\\n' "$LEARNINGS"
fi
exit 0
"""

    # --- pre-read.sh: summarize large files before Claude reads them ---
    pre_read = f"""#!/bin/sh
INPUT=$(cat)
FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('file_path') or d.get('path') or '')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then exit 0; fi

RESULT=$({agora_bin} summarize "$FILE_PATH" --json-output 2>/dev/null)
if [ -z "$RESULT" ]; then exit 0; fi

ACTION=$(printf '%s' "$RESULT" | python3 -c "
import sys, json
try:
    print(json.loads(sys.stdin.read()).get('action', 'allow'))
except Exception:
    print('allow')
" 2>/dev/null)

if [ "$ACTION" = "summarize" ]; then
    printf '%s' "$RESULT" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(d.get('summary', ''))
print()
print(f'[Read blocked: file has {{d.get(\\\"original_lines\\\", 0)}} lines. Use the summary above — do NOT read this file in chunks.]')
" 2>/dev/null
    exit 2
fi
exit 0
"""

    on_subagent = f"""#!/bin/sh
INPUT=$(cat)

# Block Explore subagent — it bypasses agora-code hooks (pre-read, on-read, etc.)
# All file exploration must go through Read/Grep/Glob in the main session.
IS_EXPLORE=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    t = str(d.get('subagent_type', d.get('type', d.get('agent_type', '')))).lower()
    print('yes' if 'explore' in t else 'no')
except Exception:
    print('no')
" 2>/dev/null)

if [ "$IS_EXPLORE" = "yes" ]; then
    printf 'agora-code: Explore subagent blocked. Use Read/Grep/Glob directly in the main session so hooks fire and files get indexed.\\n'
    exit 1
fi

CONTEXT=$({agora_bin} inject --quiet --level summary 2>/dev/null)
if [ -n "$CONTEXT" ]; then
    printf '[agora-code: parent session context]\\n%s\\n' "$CONTEXT"
fi
exit 0
"""

    # --- on-stop.sh: digest conversation into memory on session end ---
    on_stop = f"""#!/bin/sh
INPUT=$(cat)

# Always checkpoint first
{agora_bin} checkpoint --quiet 2>/dev/null || true

# Use last_assistant_message from hook input directly — no JSONL parsing needed
LAST_MSG=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('last_assistant_message', ''))
except Exception:
    print('')
" 2>/dev/null)

PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$LAST_MSG" ]; then exit 0; fi

python3 - "$LAST_MSG" "$PROMPT" << 'EOF'
import sys, subprocess, shutil, re

last_msg = sys.argv[1].strip()
prompt = sys.argv[2].strip() if len(sys.argv) > 2 else ''

FILLER = re.compile(
    r'^(hi|hey|hello|ok|okay|yes|no|sure|thanks|bye|lol|cool|great|nice|yep|nope|got it)\\b',
    re.I
)

def is_substantive(text):
    t = text.strip()
    if len(t) < 30:
        return False
    if FILLER.match(t):
        return False
    if t.startswith("agora-code "):
        return False
    return True

if not is_substantive(last_msg):
    sys.exit(0)

agora_bin = "agora-code"

# Build summary from prompt (goal) + Claude's first meaningful line (finding)
first_line = last_msg.split('\\n')[0][:150].strip()
summary_parts = []
if prompt and is_substantive(prompt):
    summary_parts.append(f"Session goal: {{prompt[:120]}}")
if first_line:
    summary_parts.append(f"Claude found: {{first_line}}")

if not summary_parts:
    sys.exit(0)

summary = " — ".join(summary_parts)

subprocess.run(
    [agora_bin, "learn", summary, "--confidence", "confirmed", "--tags", "conversation-summary"],
    capture_output=True
)
EOF

exit 0
"""

    on_tool_failure = f"""#!/bin/sh
INPUT=$(cat)
ERROR_INFO=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    tool = d.get('tool_name', 'unknown')
    err = d.get('error', '') or ''
    ti = d.get('tool_input', {{}})
    if isinstance(ti, str):
        import json as j2; ti = j2.loads(ti)
    path = ti.get('file_path') or ti.get('path') or ti.get('command') or ''
    print(f'{{tool}} failed on {{path}}: {{err[:200]}}') if err else print('')
except Exception:
    print('')
" 2>/dev/null)
if [ -n "$ERROR_INFO" ]; then
    {agora_bin} learn "$ERROR_INFO" --confidence hypothesis --tags tool-failure 2>/dev/null || true
fi
exit 0
"""

    # --- on-read.sh: auto-index symbols after Claude reads a code file ---
    on_read = f"""#!/bin/sh
INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f:
    try:
        hook = json.load(_f)
    except Exception:
        sys.exit(0)

file_path = (hook.get('tool_input') or {{}}).get('file_path', '')
if not file_path or not os.path.isfile(file_path):
    sys.exit(0)

CODE_EXTS = {{'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php'}}
if not any(file_path.endswith(e) for e in CODE_EXTS):
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    project_id = _get_project_id()
    branch = _get_git_branch()
    commit_sha = _get_commit_sha()
except Exception:
    project_id = branch = commit_sha = None

# Skip if already indexed at this commit
if commit_sha:
    try:
        import sqlite3
        db_path = os.path.expanduser(os.environ.get('AGORA_CODE_DB', '~/.agora-code/memory.db'))
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            'SELECT 1 FROM symbol_notes WHERE file_path=? AND commit_sha=? LIMIT 1',
            (file_path, commit_sha)
        ).fetchone()
        conn.close()
        if row:
            sys.exit(0)
    except Exception:
        pass

try:
    from agora_code.indexer import index_file
    index_file(file_path, project_id=project_id, branch=branch, commit_sha=commit_sha)
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0
"""

    # --- on-grep.sh: PostToolUse(Grep): index files matched by grep results ---
    on_grep = f"""#!/bin/sh
INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os
from pathlib import Path

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f:
    try:
        hook = json.load(_f)
    except Exception:
        sys.exit(0)

response = str(hook.get('tool_response', ''))
CODE_EXTS = {{'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php','.sh'}}
seen = set()
for line in response.splitlines():
    # files_with_matches mode: just a path; content mode: path:linenum:text
    candidate = line.split(':')[0].strip()
    if candidate and candidate not in seen and os.path.isfile(candidate):
        if Path(candidate).suffix.lower() in CODE_EXTS:
            seen.add(candidate)

if not seen:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    project_id = _get_project_id()
    branch = _get_git_branch()
    commit_sha = _get_commit_sha()
except Exception:
    project_id = branch = commit_sha = None

try:
    from agora_code.indexer import index_file
    for fp in seen:
        index_file(fp, project_id=project_id, branch=branch, commit_sha=commit_sha)
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0
"""

    # --- on-edit.sh: re-index symbols after Claude edits a file ---
    on_edit = f"""#!/bin/sh
INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os, subprocess

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f:
    try:
        hook = json.load(_f)
    except Exception:
        sys.exit(0)

file_path = (hook.get('tool_input') or {{}}).get('file_path', '')
if not file_path or not os.path.isfile(file_path):
    sys.exit(0)

CODE_EXTS = {{'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php'}}
if not any(file_path.endswith(e) for e in CODE_EXTS):
    sys.exit(0)

try:
    subprocess.run(['{agora_bin}', 'track-diff', file_path], capture_output=True, timeout=10)
except Exception:
    pass

try:
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import index_file
    index_file(file_path, project_id=_get_project_id(), branch=_get_git_branch())
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0
"""

    # --- on-bash.sh: tag committed files when git commit detected ---
    on_bash = f"""#!/bin/sh
INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os, subprocess

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f:
    try:
        hook = json.load(_f)
    except Exception:
        sys.exit(0)

command = (hook.get('tool_input') or {{}}).get('command', '')
if 'git' not in command or 'commit' not in command:
    sys.exit(0)

try:
    r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, timeout=5)
    commit_sha = r.stdout.strip() if r.returncode == 0 else ''
    if not commit_sha:
        sys.exit(0)
    r = subprocess.run(
        ['git', 'diff-tree', '--no-commit-id', '-r', '--name-only', commit_sha],
        capture_output=True, text=True, timeout=5
    )
    files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
except Exception:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import tag_commit
    tag_commit(commit_sha, files, project_id=_get_project_id(), branch=_get_git_branch())
except Exception:
    pass

try:
    subprocess.run(['{agora_bin}', 'learn-from-commit', commit_sha, '--quiet'],
                   timeout=30, capture_output=True)
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0
"""

    # Write files
    hooks_json_path.write_text(hooks_json, encoding="utf-8")

    scripts = {
        "on-prompt.sh": on_prompt,
        "on-read.sh": on_read,
        "on-grep.sh": on_grep,
        "on-edit.sh": on_edit,
        "on-bash.sh": on_bash,
        "on-stop.sh": on_stop,
        "on-subagent.sh": on_subagent,
        "on-tool-failure.sh": on_tool_failure,
        "pre-read.sh": pre_read,
    }
    for name, content in scripts.items():
        p = hooks_dir / name
        p.write_text(content, encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Install SKILL.md — user-global only (not project-level, to avoid duplication)
    skill_md_content = _get_skill_md_content()
    skill_path = None
    if skill_md_content:
        global_skill_dir = Path.home() / ".claude" / "skills" / "agora-code"
        global_skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = global_skill_dir / "SKILL.md"
        skill_path.write_text(skill_md_content, encoding="utf-8")

    # Register agora-memory MCP server via .mcp.json (Claude Code's MCP config file)
    import json as _json
    mcp_path = Path(".mcp.json")
    mcp_config = {}
    if mcp_path.exists():
        try:
            mcp_config = _json.loads(mcp_path.read_text(encoding="utf-8"))
        except Exception:
            mcp_config = {}
    mcp_config.setdefault("mcpServers", {})["agora-memory"] = {
        "command": agora_bin,
        "args": ["memory-server"],
    }
    mcp_path.write_text(_json.dumps(mcp_config, indent=2), encoding="utf-8")

    _echo("Installed:")
    _echo(f"   {hooks_json_path}")
    for name in scripts:
        _echo(f"   {hooks_dir / name}")
    if skill_md_content:
        _echo(f"   {skill_path}")
    _echo(f"   {mcp_path}")
    _echo("")
    _echo("Restart Claude Code in this directory to activate.")
    _echo("At the start of each session, run /agora-code to load the skill.")


# --------------------------------------------------------------------------- #
#  summarize                                                                   #
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("file_path")
@click.option("--max-tokens", default=500, help="Token budget for summary")
@click.option("--json-output", "json_out", is_flag=True, default=False,
              help="Output JSON for hook consumption")
@click.option("--threshold", default=50, help="Line threshold — files below this pass through")
def summarize(file_path, max_tokens, json_out, threshold):
    """Summarize a file's structure for token-efficient context injection.

    Uses cached AST from DB when the file was already indexed at the same git
    commit (no re-read from disk). Otherwise reads from disk and summarizes.

    \b
    Used by preToolUse hooks to intercept large file reads:
      agora-code summarize agora_code/session.py
      agora-code summarize package.json --json-output

    Files under --threshold lines return empty (signal: let it through).
    """
    from agora_code.summarizer import summarize_file, FILE_SIZE_THRESHOLD
    import os

    path = Path(file_path).resolve()

    # Restrict to paths the user actually owns: CWD subtree or home subtree.
    # This prevents hooks from being weaponised to read system files.
    _allowed_roots = [Path.cwd().resolve(), Path.home().resolve()]
    if not any(str(path).startswith(str(r)) for r in _allowed_roots):
        if json_out:
            click.echo(json.dumps({"action": "allow", "reason": "path outside allowed roots"}))
        return

    if not path.exists():
        if json_out:
            click.echo(json.dumps({"action": "allow", "reason": "file not found"}))
        return

    # Use cached AST from DB when we have a snapshot at the same git commit (no disk read).
    try:
        from agora_code.vector_store import get_store
        from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
        from agora_code.summarizer import estimate_tokens as _est_tokens
        store = get_store()
        pid = _get_project_id()
        branch = _get_git_branch()
        current_sha = _get_commit_sha()
        snapshot = store.get_file_snapshot(str(path), project_id=pid, branch=branch)
        if snapshot and snapshot.get("summary") and snapshot.get("commit_sha") == current_sha:
            summary = snapshot["summary"]
            if json_out:
                click.echo(json.dumps({
                    "action": "summarize",
                    "parser": "cached",
                    "summary": summary,
                    "original_lines": 0,
                    "original_tokens": 0,
                    "summary_tokens": _est_tokens(summary),
                }))
            else:
                _echo(f"📄 {file_path}: served from DB (cached at {current_sha or 'n/a'})\n")
                click.echo(summary)
            return
    except Exception:
        pass

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        if json_out:
            click.echo(json.dumps({"action": "allow", "reason": "unreadable"}))
        return

    summary = summarize_file(str(file_path), content, max_tokens=max_tokens, threshold=threshold)

    if summary is None:
        if json_out:
            click.echo(json.dumps({"action": "allow", "reason": "below threshold"}))
        else:
            _echo(f"✅ {file_path}: {len(content.splitlines())} lines — below threshold, pass through")
        return

    from agora_code.summarizer import estimate_tokens
    original_tokens = estimate_tokens(content)
    summary_tokens = estimate_tokens(summary)
    reduction = round((1 - summary_tokens / original_tokens) * 100, 1) if original_tokens > 0 else 0

    # Extract parser tag from summary footer
    parser = "unknown"
    for line in summary.splitlines()[-3:]:
        if line.startswith("[parser="):
            parser = line[8:].rstrip("]")
            break

    if json_out:
        click.echo(json.dumps({
            "action": "summarize",
            "parser": parser,
            "summary": summary,
            "original_lines": len(content.splitlines()),
            "original_tokens": original_tokens,
            "summary_tokens": summary_tokens,
        }))
    else:
        _echo(f"📊 {file_path}: {original_tokens} → {summary_tokens} tokens ({reduction}% reduction)\n")
        click.echo(summary)


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
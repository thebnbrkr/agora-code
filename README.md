# agora-code

**Scan any codebase. Build agents from its APIs. Remember everything.**

agora-code does three things:

1. **Discovers APIs** — scans any repo (Python, JS, Go, Java, OpenAPI spec) and extracts every endpoint automatically
2. **Builds workflows** — uses an LLM to detect which APIs belong together, then generates executable multi-step agents
3. **Remembers your sessions** — compresses and restores context so your AI assistant always knows where you left off

Works standalone as a CLI tool, or as an MCP server inside Claude Desktop, Cline, Cursor, or Antigravity.

---

## Table of Contents

- [Installation](#installation)
- [Quick Start (5 minutes)](#quick-start)
- [Daily Coding Companion (memory-server)](#daily-coding-companion)
- [Session Memory](#session-memory--context-compression)
- [Workflow Builder (agentify)](#workflow-builder)
- [MCP Server Setup](#mcp-server-setup)
- [CLI Reference](#cli-reference)
- [Project Structure](#project-structure)
- [Environment Variables](#environment-variables)

---

## Installation

```bash
pip install agora-code
```

Optional extras:
```bash
pip install agora-code[memory]   # semantic search (sqlite-vec)
pip install agora-code[claude]   # Anthropic SDK for workflow detection
pip install "agora-code[openai]" # OpenAI SDK
pip install agora-code[gemini]   # Gemini SDK
```

---

## Quick Start

### Step 1 — Scan a codebase

```bash
# Local FastAPI / Flask / Django project
agora-code scan ./my-api

# Remote OpenAPI/Swagger spec (no code needed)
agora-code scan https://petstore.swagger.io/v2/swagger.json

# Any language — LLM auto-activates if AST finds nothing
ANTHROPIC_API_KEY=... agora-code scan ./my-node-app
```

Example output:
```
✅ Tier 2 (Python AST): 8 routes from './my-api'

METHOD   PATH                     PARAMS
GET      /products                
GET      /products/{id}           id
GET      /products/search         q, max_price
POST     /orders                  order
GET      /orders/{id}             id
DELETE   /orders/{id}             id
GET      /users/{id}              id
GET      /health                  
```

### Step 2 — Start a session

```bash
agora-code checkpoint --goal "Debug POST /orders failing for new users"
```

### Step 3 — Serve as MCP tools

```bash
# Start your API server first (any port — we recommend not using 8000)
uvicorn main:app --port 7755

# Then serve agora-code as MCP
agora-code serve ./my-api --url http://localhost:7755
```

Your AI assistant can now call your API directly as tools.

---

## Session Memory & Context Compression

The biggest problem when working with AI assistants: they forget everything between sessions. You spend 2 hours figuring out that `POST /users` rejects `+` in emails, and next session you start from scratch.

agora-code solves this with a session file and compression system.

### How it works

```
You work on something
      ↓
agora-code checkpoint --goal "..." --hypothesis "..."
      ↓
.agora-code/session.json  ←  saved locally (gitignored)
      ↓
Next session: agora-code inject  →  ~120 tokens of context injected
      ↓
AI assistant instantly knows where you left off
```

### Session commands

```bash
# Save what you're working on (works for any project, not just APIs)
agora-code checkpoint --goal "Refactor auth module"
agora-code checkpoint --hypothesis "SessionManager needs a lock"
agora-code checkpoint --action "Adding retry logic to validate()"
agora-code checkpoint --file "auth.py:added retry" --file "tests/test_auth.py:updated"
agora-code checkpoint --next "Write edge case test" --blocker "Waiting for review"

# See your current session
agora-code status

# Inject context into your AI assistant
agora-code inject                   # ~200 tokens (default: summary level)  
agora-code inject --level detail    # more detail
agora-code inject --level index     # just goal + file list (~50 tokens)
agora-code inject --raw             # full session JSON

# Store a permanent finding (survives session archive)
agora-code learn "POST /users rejects + in emails (RFC-valid but API rejects)"
agora-code learn "Rate limit: 100 req/min on /data endpoints" --tags rate-limit --confidence confirmed
agora-code learn "Auth: Service A sends X-Service-Token to Service B" --tags auth,microservice

# Search your knowledge base
agora-code recall "email"           # finds email-related learnings
agora-code recall "rate limit"      # semantic if API key set, keyword otherwise

# Archive session to long-term memory
agora-code complete --summary "Fixed email validation, POST /users works"

# Browse and restore past sessions
agora-code restore                  # list recent sessions
agora-code restore 2026-03-08-fix-post-users  # restore specific session
```

### Claude Code hooks (auto-inject on every session)

Create `.claude/settings.json` in your project:
```json
{
  "hooks": {
    "PreToolUse": [
      {"command": "agora-code inject"}
    ]
  }
}
```

Claude will automatically see your session context without you having to paste anything.

### Compression levels explained

Instead of dumping thousands of tokens into context:

| Level | What's included | ~Tokens |
|---|---|---|
| `index` | Goal + file list | 50 |
| `summary` | + hypothesis + discoveries + next steps | 200 |
| `detail` | + all attempts, decisions, blockers | 500 |
| `full` | Raw JSON (no compression) | 3,000+ |

`inject` auto-picks the richest level that fits your token budget (default 2,000 tokens).

---

## Daily Coding Companion

The fastest way to use agora-code with any AI assistant — no project path, no running API needed. Just persistent memory across all your coding sessions.

**One session = one task/goal**, not one message. It spans multiple conversations until you call `complete_session`.

### Add to Antigravity / Claude Desktop

```bash
which agora-code   # find your path, e.g. /usr/local/bin/agora-code
```

Add to MCP config (Antigravity: Agent panel → MCP Servers → Manage → View raw config):
```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/usr/local/bin/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

If installed in a venv:
```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "python3",
      "args": ["-m", "agora_code.cli", "memory-server"]
    }
  }
}
```

Restart Antigravity. The AI now has 6 memory tools:

| Tool | What it does |
|---|---|
| `get_session_context` | AI auto-reads on startup — knows what you were working on |
| `save_checkpoint` | Saves goal, hypothesis, files changed |
| `store_learning` | Stores permanent findings across all projects |
| `recall_learnings` | Searches past findings (semantic if API key set) |
| `complete_session` | Archives session when task is done |
| `get_memory_stats` | Storage stats |

### What gets stored

**`.agora-code/session.json`** — active session, project-local, gitignored:
```json
{
  "goal": "Refactor auth module",
  "hypothesis": "SessionManager needs a lock",
  "files_changed": [{"file": "auth.py", "what": "added retry logic"}],
  "next_steps": ["Write edge case test"],
  "discoveries": [{"finding": "validate() not thread-safe", "confidence": "confirmed"}]
}
```

**`~/.agora-code/memory.db`** — global SQLite, permanent learnings across all projects:
```
finding                           | tags          | confidence
POST /users rejects + in emails   | auth,email    | confirmed
Rate limit: 100 req/min           | rate-limit    | confirmed
```

---

## Workflow Builder

`agentify` scans your API routes and uses an LLM to detect which endpoints naturally belong together in a sequence, then builds an executable workflow.

```bash
# Detect workflows + show DAG
agora-code agentify ./my-api --show-mermaid

# Save generated Python workflow files
agora-code agentify ./my-api --output ./workflows

# Override LLM provider
agora-code agentify ./my-api --llm-provider claude
```

Example — scanning an e-commerce API:
```
✅ 8 routes found via ast extractor
🤖 Detecting workflows with LLM (auto)...

✅ 1 workflow(s) detected:

  ◆  purchase_workflow
     Search for products, add to cart, and checkout
     Steps: GET /products/search → POST /cart/add → POST /cart/checkout
     Triggers: buy, order, purchase, shop

📊 DAG (Mermaid):
  graph TD
      GET_products_search --> POST_cart_add
      POST_cart_add --> POST_cart_checkout
```

The `--output` flag generates a ready-to-run Python file using the Agora framework:

```python
# auto-generated: purchase_workflow.py
# Edit before deploying

class Step1_GET_products_search(AsyncNode):
    async def exec_async(self, args):
        # calls GET /products/search
        ...

class Step2_POST_cart_add(AsyncNode):
    async def exec_async(self, args):
        # calls POST /cart/add  
        ...

flow = AsyncBatchFlow(name="purchase_workflow", start=step1)
```

### LLM provider for workflow detection

Auto-detected from environment variables (priority order):

| Provider | Env var | Default model |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` |
| Gemini | `GEMINI_API_KEY` | `gemini-2.0-flash` |

Override model: `LLM_MODEL=claude-opus-4-5 agora-code agentify ./my-api`

---

## MCP Server Setup

The MCP server exposes your scanned API routes as tools that any AI coding assistant can call directly.

### What the AI sees

When you connect agora-code as an MCP server, the AI gets:
- A tool for every discovered endpoint (full params, types, descriptions)
- A `🔄 SESSION RESTORED` banner showing where your last session left off (~120 tokens)
- Failure pattern hints if the same call fails 3+ times

### Claude Desktop

Add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "demo-store": {
      "command": "agora-code",
      "args": ["serve", "/path/to/your/api", "--url", "http://localhost:7755"]
    }
  }
}
```

### Cline (VS Code)

Add to Cline's MCP settings:
```json
{
  "mcpServers": {
    "my-api": {
      "command": "agora-code",
      "args": ["serve", "/path/to/your/api", "--url", "http://localhost:7755"]
    }
  }
}
```

### Antigravity

In Antigravity's "Add MCP Servers" panel, add:
```json
{
  "command": "agora-code",
  "args": ["serve", "/Users/you/your-project", "--url", "http://localhost:7755"]
}
```

### With authentication

```bash
# Bearer token
agora-code serve ./my-api --url http://localhost:7755 --auth-token mytoken

# Or set env var
AGORA_AUTH_TOKEN=mytoken agora-code serve ./my-api --url http://localhost:7755

# Interactive auth setup (saves to .agora-code/auth.json)
agora-code auth ./my-api --type bearer --token mytoken
```

---

## How Route Discovery Works

agora-code uses a 4-tier pipeline. Each tier is tried in order, and if it produces enough results, the pipeline stops.

```
Tier 1: OpenAPI / Swagger spec
        → reads spec JSON/YAML directly
        → runs if: target is a URL or spec file
        → coverage: 100% accurate

Tier 2: Python AST parser
        → reads your FastAPI/Flask/Django source code
        → runs if: target is a directory with .py files
        → coverage: ~95% accurate

Tier 3: LLM extraction  ← AUTO-ACTIVATES if Tier 1+2 find < 2 routes
        → sends source files to Claude/GPT/Gemini
        → runs if: any LLM API key is set
        → coverage: any language, any framework

Tier 4: Regex fallback
        → pattern-matches app.get(), router.post(), etc.
        → always runs as last resort
        → coverage: ~70% accurate
```

You never need to specify which tier to use — it's automatic.

---

## CLI Reference

```
agora-code scan <target>          Scan a codebase or API URL
agora-code serve <target>         Start MCP server (exposes API routes as tools)
agora-code memory-server          Start MCP server for session memory (no project needed)
agora-code agentify <target>      Auto-detect workflows, generate Agora AsyncFlow code
agora-code chat <target>          Chat with your API directly
agora-code auth <target>          Set up authentication for API calls

agora-code checkpoint             Save session state
agora-code status                 Show current session + memory stats
agora-code inject                 Print compressed context (for Claude hooks)
agora-code complete               Archive session to long-term memory
agora-code restore                Browse and restore past sessions

agora-code learn "<finding>"      Store a permanent finding
agora-code recall "<query>"       Search your knowledge base
```

---

## Project Structure

```
agora-code/
├── agora_code/
│   ├── scanner.py          4-tier pipeline orchestrator
│   ├── agent.py            MCP server for API routes (JSON-RPC 2.0 over stdio)
│   ├── memory_server.py    MCP server for session memory (project-agnostic)
│   ├── cli.py              All CLI commands
│   ├── workflows.py        Workflow detection + Agora AsyncFlow builder
│   ├── session.py          JSON session lifecycle manager
│   ├── tldr.py             Context compression (index/summary/detail/full)
│   ├── vector_store.py     SQLite + sqlite-vec + FTS5 (learnings + API logs)
│   ├── embeddings.py       OpenAI / Gemini / keyword auto-selection
│   ├── models.py           Route, Param, RouteCatalog dataclasses
│   └── extractors/
│       ├── openapi.py      Tier 1: OpenAPI spec parser
│       ├── python_ast.py   Tier 2: Python AST (FastAPI, Flask, Django)
│       ├── llm.py          Tier 3: LLM extraction (auto-escalates)
│       └── regex.py        Tier 4: Regex fallback
├── tests/                  60 tests — run with: pytest tests/
└── pyproject.toml
```

### Storage

```
.agora-code/session.json      Active session (project-local, gitignored)
~/.agora-code/memory.db       Learnings + API call history (global)
```

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Claude for LLM extraction + workflow detection |
| `OPENAI_API_KEY` | Enables GPT for LLM extraction + workflow detection |
| `GEMINI_API_KEY` | Enables Gemini for LLM extraction + workflow detection |
| `LLM_PROVIDER` | Force a provider: `claude` / `openai` / `gemini` |
| `LLM_MODEL` | Override default model: `claude-opus-4-5`, `gpt-4o`, etc. |
| `AGORA_AUTH_TOKEN` | Default bearer token for API calls |
| `AGORA_CODE_DB` | Override memory DB path (default: `~/.agora-code/memory.db`) |

---

## Testing Against the Demo API

A demo FastAPI store is included for testing:

```bash
# Install requirements
pip install fastapi uvicorn

# Start the demo API (we use 7755, not 8000)
uvicorn main:app --port 7755 --app-dir ./demo_api

# In another terminal — scan and serve
agora-code serve ./demo_api --url http://localhost:7755

# Try the workflow builder
OPENAI_API_KEY=... agora-code agentify ./demo_api --show-mermaid
```

---

## What agora-code is NOT

- Not an API proxy or gateway
- Not a hosted service (everything is local)
- Not a replacement for Postman (this is for AI-assisted development sessions)
- Not dependent on any cloud service (works fully offline with keyword search only)

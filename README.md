# agora-code

**Persistent memory layer + API scanner for AI coding agents.**

agora-code does two things:

1. **Persistent session memory** — your AI assistant always knows where you left off, what you discovered, and what changed. Survives context window resets, new conversations, and multiple agents.
2. **API discovery + MCP server** — scans any codebase (Python, OpenAPI spec) and exposes every endpoint as an MCP tool so an AI can call your API directly.

Works with **Cursor**, **Claude Code**, **Gemini CLI**, **Copilot CLI**, **Cline**, and any MCP-compatible coding assistant.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Installation](#installation)
- [Connect to MCP](#connect-to-mcp)
- [Hook Setup By Agent](#hook-setup-by-agent)
- [Session Lifecycle](#session-lifecycle)
- [MCP Tools Reference](#mcp-tools-reference-10-tools)
- [CLI Reference](#cli-reference)
- [Embeddings — Semantic Search](#embeddings--semantic-search)
- [Project Scoping](#project-scoping)
- [File Change Tracking](#file-change-tracking)
- [Git Integration](#git-integration)
- [Team Namespaces](#team-namespaces)
- [API Discovery + MCP Server](#api-discovery--mcp-server)
- [Workflow Builder](#workflow-builder)
- [Storage Architecture](#storage-architecture)
- [Compression Levels](#compression-levels)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)

---

## How It Works

The core problem: AI coding assistants forget everything between sessions. You spend an hour figuring out that a certain endpoint rejects `+` in emails, or that a particular middleware is the cause of a bug — and next session, you start from scratch.

agora-code solves this with three layers:

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: .agora-code/session.json  (project-local)      │
│  Active working memory — goal, hypothesis, discoveries.  │
│  Auto-saved on every checkpoint. Gitignored.             │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Layer 2: ~/.agora-code/memory.db  (global SQLite)       │
│  Long-term memory — archived sessions, learnings,        │
│  file change history. Persists across projects.          │
│  Scoped per project via git remote URL (project_id).     │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Semantic / keyword search                      │
│  sqlite-vec for vector similarity (optional).            │
│  FTS5/BM25 keyword search — always works, zero config.   │
│  Local embeddings via sentence-transformers (offline).   │
└─────────────────────────────────────────────────────────┘
```

### What happens at session start

```
You open a new chat
        ↓
sessionStart hook fires → session-start.sh runs
        ↓
agora-code inject:
  1. Reads .agora-code/session.json (current session)
  2. If context is empty → queries ~/.agora-code/memory.db:
       - Most recent session for this project
       - Top stored learnings for this project
     Writes recall summary into session.context (write-once cache)
  3. Compresses to ~200 tokens
  4. Returns {"additional_context": "..."} JSON to Cursor/Claude
        ↓
Agent already knows your goal, hypothesis, files changed, next steps.
Zero re-explanation needed.
```

---

## Installation

```bash
pip install git+https://github.com/thebnbrkr/agora-code
```

Optional extras:

```bash
# Local embeddings — fully offline, no API key needed
pip install "git+https://github.com/thebnbrkr/agora-code[local]"

# OpenAI embeddings
pip install "git+https://github.com/thebnbrkr/agora-code[openai]"

# Gemini embeddings + LLM scan
pip install "git+https://github.com/thebnbrkr/agora-code[gemini]"

# Everything
pip install "git+https://github.com/thebnbrkr/agora-code[all]"
```

Find your binary path (needed for MCP config):

```bash
which agora-code
# e.g. /Library/Frameworks/Python.framework/Versions/3.10/bin/agora-code
```

---

## Connect to MCP

The memory server is a project-agnostic MCP server. No running API needed. Add it to your AI assistant once and it works for every project.

> **Use the full binary path** — most IDEs don't inherit your shell PATH when spawning MCP processes. Use the output of `which agora-code`.

### Cursor

Go to **Settings → MCP** → click "Edit in settings.json" and add:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/full/path/to/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

Or via Cursor's MCP panel: command = `/full/path/to/agora-code`, args = `["memory-server"]`.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/full/path/to/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

### Claude Code (Antigravity)

Edit `~/.config/claude/config.json` or use the Antigravity MCP panel:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/full/path/to/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

### Cline / Continue / any MCP client

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/full/path/to/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

### Using a virtualenv

If agora-code is installed in a venv:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/path/to/venv/bin/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

Or use python directly:

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

### Verify it's working

Once connected, ask your AI:

> "What am I working on?"

If the MCP is connected and a session exists, the agent will call `get_session_context` and return your current goal and state. No explanation from you needed.

---

## Hook Setup By Agent

Hooks auto-inject context at session start, save state before context compaction, and track file changes. Set them up once per project.

### Cursor

Create `.cursor/hooks.json` in your project root:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      {"command": ".cursor/hooks/session-start.sh"}
    ],
    "afterFileEdit": [
      {"command": ".cursor/hooks/after-file-edit.sh"}
    ],
    "preCompact": [
      {"command": ".cursor/hooks/pre-compact.sh"}
    ]
  }
}
```

Create the three shell scripts:

**`.cursor/hooks/session-start.sh`**
```sh
#!/bin/sh
# Reads Cursor's JSON from stdin, injects session context as JSON output.
# Output MUST be {"additional_context": "..."} — plain text causes a parse error.
cat > /dev/null
context=$(agora-code inject --quiet 2>/dev/null)
if [ -n "$context" ]; then
    escaped=$(printf '%s' "$context" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
    printf '{"additional_context":%s}\n' "$escaped"
else
    printf '{}\n'
fi
```

**`.cursor/hooks/after-file-edit.sh`**
```sh
#!/bin/sh
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('filePath', data.get('file_path', data.get('path', ''))))
except Exception:
    print('')
" 2>/dev/null)
agora-code scan . --cache --quiet 2>/dev/null || true
if [ -n "$FILE_PATH" ]; then
    agora-code track-diff "$FILE_PATH" 2>/dev/null || true
fi
exit 0
```

**`.cursor/hooks/pre-compact.sh`**
```sh
#!/bin/sh
cat > /dev/null
agora-code checkpoint --quiet
exit 0
```

Make all scripts executable:
```bash
chmod +x .cursor/hooks/*.sh
```

> **Important:** Cursor requires `"version": 1` at the top of `hooks.json` or the file is silently ignored. Event names are camelCase: `sessionStart`, `afterFileEdit`, `preCompact`. Hook scripts must be separate files — inline commands are not supported.

### Claude Code

Create `.claude/hooks.json` in your project root:

```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "agora-code inject --quiet"}
    ],
    "PreCompact": [
      {"type": "command", "command": "agora-code checkpoint --quiet"}
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {"type": "command", "command": "agora-code scan . --cache --quiet"},
          {"type": "command", "command": "agora-code track-diff $CLAUDE_TOOL_INPUT_FILE_PATH"}
        ]
      }
    ]
  }
}
```

### Gemini CLI

Create `.gemini/settings.json` in your project root:

```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "agora-code inject --quiet || exit 0"}
    ],
    "PreCompact": [
      {"type": "command", "command": "agora-code checkpoint --quiet"}
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|write_file|replace|edit_file",
        "hooks": [
          {"type": "command", "command": "agora-code scan . --cache --quiet"},
          {"type": "command", "command": "agora-code track-diff $GEMINI_TOOL_INPUT_FILE_PATH"}
        ]
      }
    ]
  }
}
```

### Copilot CLI

Create `.github/hooks/agora-code.json`:

```json
{
  "hooks": {
    "session_start": "agora-code inject --quiet",
    "file_write": [
      "agora-code scan . --cache --quiet",
      "agora-code track-diff $COPILOT_TOOL_INPUT_FILE_PATH"
    ],
    "pre_compact": "agora-code checkpoint --quiet"
  }
}
```

### SKILL.md — Tell the AI when to use each tool

`SKILL.md` at the project root is a reference card for the AI agent. Include it in your `CLAUDE.md`, `AGENTS.md`, or equivalent system prompt file:

```
# Memory Tools
See SKILL.md for agora-memory tool usage guidelines.
```

This tells the AI to proactively call `get_session_context` at session start, `save_checkpoint` after completing steps, and `store_learning` when it discovers something non-obvious.

---

## Session Lifecycle

A **session** is a goal-oriented work period. It spans across context window resets, new conversations, and multiple agents — until you explicitly call `complete_session`.

```
Start of work
      │
      ▼
save_checkpoint(goal="Fix POST /users 422 errors")
      │  auto-captures: git branch, commit SHA, uncommitted files,
      │  ticket number (JIRA-423 from feat/JIRA-423-fix-login),
      │  goal derived from branch name if not set
      ▼
.agora-code/session.json   ← source of truth, project-local, gitignored
~/.agora-code/memory.db    ← dual-write, survives process restarts
      │
      ▼  [context window fills]
      │
preCompact hook fires → agora-code checkpoint --quiet
      │  state saved before Cursor/Claude compresses the window
      ▼
[new conversation]
      │
sessionStart hook fires → agora-code inject
      │  1. reads session.json
      │  2. if context empty → queries DB for past session + learnings
      │  3. injects ~200-500 token summary into system context
      ▼
Agent already knows the goal, hypothesis, discoveries, next steps
      │
      ▼  [task complete]
      │
complete_session(summary="Fixed 422 — email regex was too strict")
      │  archives to memory.db with embedding for semantic search
      ▼
Next session: DB recall surfaces this automatically
```

---

## MCP Tools Reference (10 tools)

The memory server exposes 10 tools to any MCP-compatible AI assistant.

### `get_session_context`

Returns compressed session state. At session start, also auto-populates context from the DB if the session is new (past session summary + top learnings for this project).

```
Parameters:
  level: "index" | "summary" | "detail" | "full"  (default: "detail")
```

### `save_checkpoint`

Saves current state to `session.json` and the SQLite DB.

```
Parameters:
  goal:          string  — what you're trying to accomplish
  hypothesis:    string  — current working theory
  action:        string  — what you're doing right now
  context:       string  — free-text project notes
  files_changed: array   — e.g. ["auth.py:added retry logic", "tests/test_auth.py"]
  next_steps:    array   — list of strings
  blockers:      array   — list of strings
```

Auto-captured on every call: git branch, HEAD commit SHA, ticket number from branch, uncommitted files.

### `store_learning`

Stores a permanent finding. Persists across sessions and projects. Embeddings stored alongside for semantic recall.

```
Parameters:
  finding:    string  — what was learned (required)
  evidence:   string  — how this was discovered
  confidence: "confirmed" | "likely" | "hypothesis"  (default: "confirmed")
  tags:       array   — list of tag strings
```

### `recall_learnings`

Searches past findings. Uses semantic search if embeddings are configured, FTS5 keyword search otherwise. Results reranked by: text relevance + recency (48h half-life) + confidence + branch match (exact: +0.30, same prefix: +0.15) + file overlap (+0.20 max).

```
Parameters:
  query: string  — what to search for (required)
  limit: integer  (default: 5)
```

### `complete_session`

Archives the current session to long-term storage with an embedding for future semantic recall.

```
Parameters:
  summary: string  — what was accomplished
  outcome: "success" | "partial" | "abandoned"  (default: "success")
```

### `get_memory_stats`

Returns session count, learning count, API call count, search mode, and DB location. No parameters.

### `list_sessions`

Lists past sessions with metadata.

```
Parameters:
  limit:  integer  (default: 20)
  branch: string   — filter by git branch (optional)
```

### `store_team_learning`

Same as `store_learning` but writes to the `team` namespace — visible to all agents sharing the same DB.

### `recall_team`

Searches the shared team knowledge base.

```
Parameters:
  query: string  (required)
  limit: integer  (default: 5)
```

### `recall_file_history`

Returns compact change history for a file — what changed, when, by which session, on which branch.

```
Parameters:
  file_path: string  — e.g. "agora_code/auth.py"  (required)
  limit:     integer  (default: 10)
```

---

## CLI Reference

### Inspect memory

```bash
# Show current session + timestamps + DB stats
agora-code status

# List all past sessions
agora-code restore

# Restore a specific past session as active
agora-code restore <session_id>

# Search learnings
agora-code recall "<query>"
agora-code recall "cursor hooks" --limit 10

# View file change history
agora-code file-history agora_code/auth.py
agora-code file-history agora_code/auth.py --limit 5
```

### Save state

```bash
# Save session state
agora-code checkpoint \
  --goal "Fix POST /users 422 errors" \
  --hypothesis "Email regex too strict" \
  --action "Testing edge cases" \
  --file "auth.py:added retry" \
  --file "tests/test_auth.py" \
  --next "Write integration test" \
  --blocker "Waiting for staging deploy"

# Store a permanent learning
agora-code learn "POST /users rejects + in email addresses" \
  --confidence confirmed \
  --tags api,validation

# Archive completed session
agora-code complete --summary "Fixed 422, deployed to staging" --outcome success
```

### Context injection

```bash
# Print compressed context (used by hooks)
agora-code inject
agora-code inject --level detail
agora-code inject --token-budget 1000   # auto-pick level to fit
agora-code inject --raw                 # full session JSON

# Start MCP memory server
agora-code memory-server
```

### File tracking

```bash
# Capture git diff for a file → store summary
agora-code track-diff agora_code/auth.py
agora-code track-diff agora_code/auth.py --committed  # vs last commit

# Install git post-commit hook (auto-tracks on every commit)
agora-code install-hooks
agora-code install-hooks --force  # overwrite existing
```

### API scanning

```bash
agora-code scan <target>           # scan codebase or OpenAPI spec
  --cache                          # use cached results
  --quiet                          # suppress output (for hooks)

agora-code serve <target> --url http://localhost:7755   # start API MCP server
agora-code stats <target>          # show API call stats
agora-code chat <target> --url http://localhost:7755    # interactive chat
agora-code agentify <target>       # detect workflows, generate code
```

---

## Embeddings — Semantic Search

Embeddings convert text to vectors so semantically similar content is found even when exact keywords don't match. agora-code works without any embeddings (FTS5 keyword search always works), but embeddings make recall significantly better.

### Provider priority (auto-detected)

```
EMBEDDING_PROVIDER=auto (default):
  1. OpenAI  text-embedding-3-small  (1536 dims) — set OPENAI_API_KEY
  2. Gemini  gemini-embedding-001    (768 dims)  — set GEMINI_API_KEY or GOOGLE_API_KEY
  3. Local   BAAI/bge-small-en-v1.5  (384 dims)  — install sentence-transformers
  4. None    → FTS5 keyword search only
```

### Force a specific provider

```bash
# Fully offline — no API key, no internet
export EMBEDDING_PROVIDER=local
pip install "git+https://github.com/thebnbrkr/agora-code[local]"

# Or override the local model
export LOCAL_EMBEDDING_MODEL=BAAI/bge-large-en-v1.5  # 1024 dims, more accurate
```

### Check which provider is active

```bash
agora-code status
# Shows: [vector search: on (openai)] or [vector search: off (install sqlite-vec)]

# Or from Python:
python3 -c "from agora_code.embeddings import provider_info; print(provider_info())"
```

### Embedding cache

Query embeddings are cached in-process with `@lru_cache(maxsize=256)` — repeated searches for the same query string don't re-call the API. Storage embeddings (store_learning, complete_session) are always fresh.

---

## Project Scoping

agora-code uses `project_id` to scope sessions and learnings per project. This prevents context from one project bleeding into another when the global DB is shared.

**`project_id` is derived from your git remote URL:**

```bash
git remote get-url origin
# → https://github.com/you/your-project  (used as project_id)
```

Falls back to the current directory name if no git remote is set.

This means:
- `recall_learnings` only surfaces learnings from the current project by default
- `get_session_context` only recalls the last session from this project
- When you move between projects, each gets its own scoped memory

### SaaS / multi-device use

`project_id` is the natural tenant isolation key for a hosted Supabase backend. All sessions and learnings for a project are queryable with a single `WHERE project_id = ?` filter.

---

## File Change Tracking

Every time the AI edits a file, agora-code captures what changed and stores a compact summary. Over time, this builds a per-file change history queryable without reading the file.

```bash
# View change history for a file
agora-code file-history agora_code/auth.py

# Output:
# Change history for agora_code/auth.py (3 entries):
# • 2026-03-12 [main] @abc123def456: added _get_project_id(), updated update_session
# • 2026-03-11 [main]: added retry logic to validate(), updated imports
# • 2026-03-10 [feat/auth]: initial auth implementation
```

The `afterFileEdit` hook runs `track-diff` automatically on every file save. You can also run it manually:

```bash
agora-code track-diff agora_code/auth.py           # working tree diff
agora-code track-diff agora_code/auth.py --committed  # vs last commit
```

---

## Git Integration

agora-code automatically captures git context on every checkpoint — no manual input needed.

| Field | How detected | Example |
|---|---|---|
| `branch` | `git rev-parse --abbrev-ref HEAD` | `feat/auth-service` |
| `commit_sha` | `git rev-parse --short=12 HEAD` | `abc123def456` |
| `ticket` | Regex on branch name | `JIRA-423` from `JIRA-423-fix-login` |
| `uncommitted_files` | `git status --porcelain` | `["auth.py", "middleware.py"]` |
| `project_id` | `git remote get-url origin` | `https://github.com/you/repo` |
| `goal` (fallback) | Derived from branch | `"JIRA-423: fix login"` |

### Ticket extraction patterns

| Branch | Extracted ticket |
|---|---|
| `JIRA-423-fix-login` | `JIRA-423` |
| `feature/JIRA-423-login` | `JIRA-423` |
| `fix/gh-456-null-ptr` | `GH-456` |
| `GH-78-perf` | `GH-78` |
| `feat/auth-service` | *(none — goal derived as "Working on feat/auth-service")* |

---

## Team Namespaces

Multiple agents working on the same project can share a knowledge pool:

```
Agent A stores:  store_team_learning("Rate limit on /auth: 100 req/min per IP")
Agent B recalls: recall_team("rate limiting")  → finds Agent A's learning
```

For shared memory, all agents must point to the same DB:

```bash
export AGORA_CODE_DB=/shared/path/agora-memory.db
```

Or in MCP config:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/full/path/to/agora-code",
      "args": ["memory-server"],
      "env": {"AGORA_CODE_DB": "/shared/path/agora-memory.db"}
    }
  }
}
```

---

## API Discovery + MCP Server

`agora-code serve` scans a codebase and exposes every discovered endpoint as an MCP tool — your AI can call your API directly.

### Route discovery pipeline (4-tier cascade)

```
Tier 1: OpenAPI/Swagger spec      → reads spec JSON/YAML directly       (100% accurate)
Tier 2: Python AST parser         → FastAPI/Flask/Django decorators      (~95% accurate)
Tier 3: LLM extraction            → Claude/GPT/Gemini reads source       (~90% accurate)
Tier 4: Regex fallback            → pattern-matches route decorators     (~70% accurate)
```

### Connect as MCP server

```json
{
  "mcpServers": {
    "my-api": {
      "command": "/full/path/to/agora-code",
      "args": ["serve", "/path/to/project", "--url", "http://localhost:7755"]
    }
  }
}
```

With authentication:

```bash
agora-code serve ./my-api --url http://localhost:7755 --auth-token mytoken
# or via env:
AGORA_AUTH_TOKEN=mytoken agora-code serve ./my-api --url http://localhost:7755
```

---

## Workflow Builder

`agentify` detects multi-step workflows from your API routes using an LLM and generates executable Python code.

```bash
agora-code agentify ./my-api --show-mermaid
agora-code agentify ./my-api --output ./workflows --llm-provider claude
```

---

## Storage Architecture

```
.agora-code/
  session.json          Active session (project-local, gitignored)
  .gitignore            Auto-created, ignores everything in this dir

~/.agora-code/
  memory.db             SQLite database (global — all projects)
    ├── sessions         Archived session records
    │     session_id, goal, hypothesis, branch, commit_sha,
    │     ticket, status, session_data (full JSON), project_id
    ├── learnings        Permanent findings
    │     finding, evidence, confidence, tags, branch, files,
    │     namespace (personal/team), project_id
    ├── file_changes     Per-file git diff history
    │     file_path, diff_summary, diff_snippet, commit_sha,
    │     session_id, branch, timestamp
    └── api_calls        HTTP interaction log (for serve/chat mode)
```

Override the DB path:

```bash
export AGORA_CODE_DB=/path/to/custom/memory.db
```

---

## Compression Levels

`inject` and `get_session_context` support four compression levels:

| Level | What's included | ~Tokens |
|---|---|---|
| `index` | Goal + status + branch | ~50 |
| `summary` | + hypothesis + top discoveries + next steps | ~200 |
| `detail` | + all discoveries, decisions, blockers, files changed | ~500 |
| `full` | Raw JSON (no compression) | 3,000+ |

`inject` auto-picks the richest level that fits within `--token-budget` (default: 2,000 tokens).

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI embeddings + GPT LLM scan | — |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini embeddings + LLM scan | — |
| `ANTHROPIC_API_KEY` | Claude for LLM scan + workflow detection | — |
| `EMBEDDING_PROVIDER` | `auto` / `openai` / `gemini` / `local` | `auto` |
| `LOCAL_EMBEDDING_MODEL` | sentence-transformers model name | `BAAI/bge-small-en-v1.5` |
| `EMBEDDING_DEVICE` | `cpu` / `cuda` / `mps` for local model | `cpu` |
| `AGORA_CODE_DB` | Override memory DB path | `~/.agora-code/memory.db` |
| `AGORA_AUTH_TOKEN` | Default bearer token for API calls | — |
| `LLM_PROVIDER` | Force: `claude` / `openai` / `gemini` | auto |
| `LLM_MODEL` | Override default model per provider | provider default |

**Embedding fallback chain:** OpenAI → Gemini → sentence-transformers (local, fully offline) → FTS5 keyword-only. Semantic search works without any API key if `sentence-transformers` is installed.

---

## Project Structure

```
agora-code/
├── agora_code/
│   ├── cli.py              All CLI commands (inject, checkpoint, recall, etc.)
│   ├── memory_server.py    MCP server for session memory (10 tools, JSON-RPC 2.0)
│   ├── session.py          Session lifecycle, git helpers, DB recall, project_id
│   ├── vector_store.py     SQLite + sqlite-vec + FTS5, project_id scoping
│   ├── embeddings.py       OpenAI / Gemini / local sentence-transformers, LRU cache
│   ├── tldr.py             Context compression (index/summary/detail/full)
│   ├── agent.py            MCP server for API routes
│   ├── scanner.py          4-tier route discovery pipeline
│   ├── workflows.py        Workflow detection + code generator
│   ├── models.py           Route, Param, RouteCatalog dataclasses
│   └── extractors/
│       ├── openapi.py      Tier 1: OpenAPI/Swagger spec parser
│       ├── python_ast.py   Tier 2: FastAPI/Flask/Django AST
│       ├── llm.py          Tier 3: LLM extraction (Claude/GPT/Gemini)
│       └── regex.py        Tier 4: Regex fallback
├── .cursor/
│   ├── hooks.json          Cursor hook config (version:1, camelCase event names)
│   └── hooks/              Shell scripts (session-start.sh, after-file-edit.sh, pre-compact.sh)
├── .claude/hooks.json      Claude Code hook config
├── .gemini/settings.json   Gemini CLI hook config
├── .github/hooks/          Copilot CLI hook config
├── SKILL.md                Agent tool-usage reference card
├── tests/                  186 tests — run with: pytest tests/
└── pyproject.toml
```

---

## What agora-code is NOT

- Not a hosted service — everything runs locally (SQLite, local files)
- Not cloud-dependent — works fully offline with FTS5 keyword search
- Not an API proxy or gateway
- Not a replacement for Postman (this is for AI-assisted development)
- Not specific to Python — the memory layer works for any project; API scanning targets Python/OpenAPI

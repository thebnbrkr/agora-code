# agora-code

**Memory layer + API scanner for AI coding agents.**

agora-code does two main things:

1. **Persistent session memory** — compresses and restores context so your AI assistant always knows where you left off, what you discovered, and what changed in the codebase. Spans multiple context windows, multiple days, multiple agents.
2. **API discovery + MCP server** — scans any codebase (Python, OpenAPI spec) and exposes every endpoint as an MCP tool so an AI assistant can call your API directly.

Works with Claude Code, Cursor, Gemini CLI, Copilot CLI, Cline, and any MCP-compatible coding assistant.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Installation](#installation)
- [Quick Setup — Memory Server](#quick-setup--memory-server-5-minutes)
- [Session Lifecycle](#session-lifecycle)
- [MCP Tools Reference](#mcp-tools-reference-10-tools)
- [Git Integration](#git-integration)
- [File Change Tracking](#file-change-tracking)
- [Team Namespaces](#team-namespaces)
- [Hook Setup By Agent](#hook-setup-by-agent)
- [CLI Reference](#cli-reference)
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
│  Layer 1: Session JSON (.agora-code/session.json)        │
│  Active working memory — goal, hypothesis, discoveries   │
│  Auto-saved on every checkpoint. Gitignored.             │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Layer 2: SQLite (~/.agora-code/memory.db)               │
│  Long-term memory — archived sessions, learnings,        │
│  file change history. Persists across projects.          │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Vector/keyword search                          │
│  Semantic recall via sqlite-vec + sentence-transformers  │
│  Falls back to FTS5 keyword search (no API key needed).  │
└─────────────────────────────────────────────────────────┘
```

At session start, the AI calls `get_session_context` → reads a compressed summary (~400-600 tokens) of what you were working on → immediately knows the goal, branch, hypothesis, what you discovered, and what files changed. No reading the codebase from scratch.

---

## Installation

```bash
pip install git+https://github.com/thebnbrkr/agora-code
```

Optional extras:
```bash
pip install "git+https://github.com/thebnbrkr/agora-code[memory]"   # sqlite-vec for semantic recall
pip install "git+https://github.com/thebnbrkr/agora-code[claude]"   # Anthropic SDK
pip install "git+https://github.com/thebnbrkr/agora-code[openai]"   # OpenAI SDK
pip install "git+https://github.com/thebnbrkr/agora-code[gemini]"   # Gemini SDK
```

Find the installed path:
```bash
which agora-code   # e.g. /usr/local/bin/agora-code
```

---

## Quick Setup — Memory Server (5 minutes)

The memory server is a project-agnostic MCP server. No running API, no project directory needed. Just persistent memory for any coding session.

### Step 1 — Add to your AI assistant

**Claude Desktop / Antigravity** (`~/.config/claude/config.json` or Antigravity MCP panel):
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

If using a virtualenv:
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

### Step 2 — Install hooks for auto-injection

**Claude Code** — create `.claude/hooks.json` in your project root:
```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "agora-code inject --quiet"}
    ],
    "PreCompact": [
      {"type": "command", "command": "agora-code state save"}
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

Now every time you start a Claude Code session, your context is automatically injected. Every file write automatically tracks what changed.

### Step 3 — Read SKILL.md

`SKILL.md` in the project root tells the AI exactly when to use each memory tool. Include it in your `CLAUDE.md` or equivalent:
```
See SKILL.md for agora-memory tool usage guidelines.
```

---

## Session Lifecycle

A **session** is a goal-oriented work period. It is NOT tied to a single chat window — it spans across context limit resets, restarts, and multiple conversations until you explicitly call `complete_session`.

```
New session starts
      │
      ▼
save_checkpoint(goal="...", hypothesis="...")
      │  ← auto-detects: git branch, commit SHA, uncommitted files
      │  ← auto-derives: ticket number from branch name (JIRA-123, gh-456)
      │  ← auto-derives: goal from branch name if none set
      ▼
.agora-code/session.json  (project-local, gitignored)
~/.agora-code/memory.db   (dual-write, no data loss)
      │
      ▼  [context window fills → new chat starts]
      │
SessionStart hook fires
      │
      ▼
agora-code inject  →  AI reads ~500 token summary
      │  "Goal: JIRA-423: fix login [feat/login-fix]
      │   Hypothesis: middleware rejects non-ASCII usernames
      │   Discoveries: POST /auth returns 400 for usernames with spaces
      │   Files changed: auth.py, middleware.py"
      ▼
Session continues seamlessly
      │
      ▼ [task done]
      │
complete_session(summary="Fixed login bug, deployed to staging")
      │
      ▼
Session archived to SQLite — searchable forever via recall_learnings
```

### Session identity

Sessions are identified by a combination of:
- **Branch name** (primary anchor — auto-detected from `git rev-parse`)
- **Ticket number** (auto-extracted from branch: `JIRA-423-fix-login` → `JIRA-423`)
- **Goal** (optional human note — auto-derived from branch if not set)
- **Timestamp** (always present)
- **Commit SHA** (stored on every checkpoint for future rewind)

---

## MCP Tools Reference (10 tools)

The memory server exposes 10 tools to any MCP-compatible AI assistant.

### `get_session_context`
Returns compressed session state — what you were working on, what you discovered, what changed.

```
Parameters:
  level: "index" | "summary" | "detail" | "full"  (default: "detail")
```

The AI should call this at session start. Hook-based injection (`agora-code inject`) does this automatically.

---

### `save_checkpoint`
Saves current state to `session.json` and archives to SQLite. Call this after completing any meaningful step.

```
Parameters:
  goal:          string  — what you're trying to accomplish
  hypothesis:    string  — current working theory
  action:        string  — what you're doing right now
  context:       string  — free-text project notes
  files_changed: array   — [{file: "auth.py", what: "added retry logic"}]
  next_steps:    array   — list of strings
  blockers:      array   — list of strings
```

Auto-detected on every call (no input needed):
- Current git branch
- HEAD commit SHA
- Ticket number from branch name
- Uncommitted files from `git status`
- Goal fallback from branch name if not set

---

### `store_learning`
Stores a permanent finding to the learnings table. Persists across sessions and projects.

```
Parameters:
  finding:    string  — what was learned (required)
  evidence:   string  — how this was discovered
  confidence: "confirmed" | "likely" | "hypothesis"  (default: "confirmed")
  tags:       array   — list of tag strings
```

Embeddings are stored alongside the text if an API key is configured, enabling semantic recall.

---

### `recall_learnings`
Searches past findings semantically (or by keyword if no embedding API key). Automatically enriched with active session context (branch, goal, current files) for more relevant results.

```
Parameters:
  query: string  — what to search for (required)
  limit: integer  (default: 5)
```

Results are reranked by:
1. Semantic similarity to query
2. Recency (newer findings ranked higher)
3. Confidence level
4. Branch match (+0.30 exact match, +0.15 same prefix e.g. `feat/*`)
5. File overlap (+0.20 capped, scaled by count)

---

### `complete_session`
Archives the current session to long-term SQLite storage. Call when a task is fully done.

```
Parameters:
  summary: string  — what was accomplished
  outcome: "success" | "partial" | "abandoned"  (default: "success")
```

---

### `get_memory_stats`
Returns counts of sessions, learnings, API calls, and whether vector search is active.

No parameters.

---

### `list_sessions`
Lists past sessions with metadata. Useful for finding what was worked on before.

```
Parameters:
  limit:  integer  (default: 20)
  branch: string   — filter by branch name (optional)
```

Returns: session_id, started_at, last_active, status, goal, branch, commit_sha, ticket.

---

### `store_team_learning`
Same as `store_learning` but writes to the `team` namespace — visible to all agents and teammates sharing the same DB.

```
Parameters: (same as store_learning)
```

---

### `recall_team`
Searches the shared team knowledge base.

```
Parameters:
  query: string  (required)
  limit: integer  (default: 5)
```

---

### `recall_file_history`
Returns the tracked change history for a file — what changed, when, by which session, on which branch. Compact summaries, not raw diffs.

```
Parameters:
  file_path: string  — relative path, e.g. "agora_code/auth.py"  (required)
  limit:     integer  (default: 10)
```

Use this when starting work on a file to understand recent changes without reading the full file.

---

## Git Integration

agora-code automatically captures git context on every checkpoint — no manual input.

### What gets auto-detected

| Field | How detected | Example |
|---|---|---|
| `branch` | `git rev-parse --abbrev-ref HEAD` | `feat/auth-service` |
| `commit_sha` | `git rev-parse --short=12 HEAD` | `abc123def456` |
| `ticket` | Regex on branch name | `JIRA-423` from `JIRA-423-fix-login` |
| `uncommitted_files` | `git status --porcelain` | `["auth.py", "middleware.py"]` |
| `goal` (fallback) | Derived from branch | `"JIRA-423: fix login"` |

### Ticket extraction patterns

| Branch name | Extracted ticket |
|---|---|
| `JIRA-423-fix-login` | `JIRA-423` |
| `feature/JIRA-423-login` | `JIRA-423` |
| `fix/gh-456-null-ptr` | `GH-456` |
| `GH-78-perf` | `GH-78` |
| `feat/auth-service` | *(none — goal derived as "Working on feat/auth-service")* |

### Recall scoring with branch context

When you call `recall_learnings` on branch `feat/auth`:
- Learnings from `feat/auth` get +0.30 score boost
- Learnings from `feat/*` (same prefix) get +0.15 boost
- Learnings tagged with currently-open files get up to +0.20 boost

This surfaces the most relevant past work without filtering out unrelated findings entirely.

---

## File Change Tracking

Every time the AI edits a file, agora-code captures what changed via `git diff` and stores a compact summary. Over time, this builds a per-file change history you can query without reading the file.

### How it works

1. AI writes/edits a file → PostToolUse hook fires
2. `agora-code track-diff <file>` runs `git diff HEAD -- <file>`
3. Heuristic summarizer extracts: lines added/removed, functions touched
4. Summary stored to `file_changes` table: `"+12 lines -3 lines in verify_token, get_user"`
5. `recall_file_history("auth.py")` returns entire change log

### Example output

```
$ agora-code file-history agora_code/auth.py

📋 Change history for agora_code/auth.py (4 entries):

  2026-03-11 [feat/auth] @abc123def456
    agora_code/auth.py: +8 lines -2 lines in verify_token (session: 2026-03-11-feat-auth)

  2026-03-10 [feat/auth]
    agora_code/auth.py: +15 lines in get_user, validate_token (session: 2026-03-10-debug-auth)

  2026-03-09 [main]
    agora_code/auth.py: +3 lines -1 lines in verify_token (session: 2026-03-09-...)
```

### Manual tracking

```bash
# Track working-tree changes (unstaged/staged but uncommitted)
agora-code track-diff agora_code/auth.py

# Track changes vs last commit
agora-code track-diff agora_code/auth.py --committed

# View change log
agora-code file-history agora_code/auth.py
agora-code file-history agora_code/auth.py --limit 5
```

---

## Team Namespaces

Multiple agents working on the same project can share a knowledge pool via the team namespace.

```
Agent A (planner)                    Agent B (coder)
     │                                     │
     │ store_team_learning(                │
     │   "Rate limit on /auth:             │
     │    100 req/min per IP"              │
     │ )                                   │
     │                                     │
     └──────────── memory.db ─────────────→│
                                           │
                                    recall_team(
                                      "rate limiting"
                                    )
                                    # finds Agent A's learning
```

### Setup for multi-agent use

All agents sharing memory must point to the same DB. Set `AGORA_CODE_DB`:

```bash
export AGORA_CODE_DB=/shared/path/agora-memory.db
agora-code memory-server
```

Or in MCP config:
```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "agora-code",
      "args": ["memory-server"],
      "env": {"AGORA_CODE_DB": "/shared/path/agora-memory.db"}
    }
  }
}
```

Personal learnings (`store_learning`) are isolated per agent. Team learnings (`store_team_learning`) are shared across all agents in the same namespace.

---

## Hook Setup By Agent

### Claude Code

File: `.claude/hooks.json`

```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "agora-code inject --quiet"}
    ],
    "PreCompact": [
      {"type": "command", "command": "agora-code state save"}
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

File: `.gemini/settings.json`

```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "agora-code inject --quiet"}
    ],
    "PreCompact": [
      {"type": "command", "command": "agora-code state save"}
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|write_file|edit_file",
        "hooks": [
          {"type": "command", "command": "agora-code scan . --cache --quiet"},
          {"type": "command", "command": "agora-code track-diff $GEMINI_TOOL_INPUT_FILE_PATH"}
        ]
      }
    ]
  }
}
```

### Cursor

File: `.cursor/hooks.json`

```json
{
  "hooks": {
    "onConversationStart": [
      {"command": "agora-code inject --quiet"}
    ],
    "onFileWrite": [
      {"command": "agora-code scan . --cache --quiet"},
      {"command": "agora-code track-diff $CURSOR_TOOL_INPUT_FILE_PATH"}
    ],
    "onContextLimit": [
      {"command": "agora-code state save"}
    ]
  }
}
```

### Copilot CLI

File: `.github/hooks/agora-code.json`

```json
{
  "hooks": {
    "session_start": "agora-code inject --quiet",
    "file_write": [
      "agora-code scan . --cache --quiet",
      "agora-code track-diff $COPILOT_TOOL_INPUT_FILE_PATH"
    ],
    "pre_compact": "agora-code state save"
  }
}
```

---

## CLI Reference

### Memory commands

```bash
agora-code checkpoint              Save session state to disk and SQLite
  --goal "..."                     What you're trying to accomplish
  --hypothesis "..."               Current working theory
  --action "..."                   What you're doing right now
  --context "..."                  Free-text project notes
  --file "auth.py:added retry"     File changed (repeatable, with optional note)
  --next "..."                     Next step (repeatable)
  --blocker "..."                  Blocker (repeatable)

agora-code inject                  Print compressed context (for agent injection)
  --level index|summary|detail|full
  --token-budget 2000              Auto-pick level to fit budget
  --raw                            Print full session JSON

agora-code status                  Show active session + memory stats

agora-code learn "<finding>"       Store a permanent learning
  --evidence "..."
  --confidence confirmed|likely|hypothesis
  --tags tag1,tag2

agora-code recall "<query>"        Search the knowledge base
  --limit 10

agora-code complete                Archive session to long-term memory
  --summary "..."
  --outcome success|partial|abandoned

agora-code restore                 List and restore past sessions
agora-code restore <session_id>    Restore specific session as active

agora-code track-diff <file>       Capture git diff for a file → store summary
  --committed                      Diff vs HEAD~1 instead of working tree

agora-code file-history <file>     View change log for a file (with author attribution)
  --limit 20

agora-code install-hooks           Install git post-commit hook (fires on every commit)
  --force                          Overwrite existing hook

agora-code memory-server           Start project-agnostic MCP memory server
```

### API scanning commands

```bash
agora-code scan <target>           Scan a codebase or API spec URL
  --use-llm                        Force LLM extraction
  --cache                          Use cached results if available
  --quiet                          Suppress output (for hook use)

agora-code serve <target>          Start MCP server for API routes
  --url http://localhost:7755      Base URL of the live API (required)
  --auth-token <token>             Bearer token
  --auth-type bearer|api-key|basic|none

agora-code chat <target>           Interactive chat session with your API
  --url http://localhost:7755

agora-code auth <target>           Configure auth for API calls
agora-code stats <target>          Show API call stats from memory

agora-code agentify <target>       Detect workflows, generate agent code
  --output ./workflows             Save generated Python files
  --show-mermaid                   Print Mermaid DAG
  --llm-provider claude|openai|gemini
```

---

## API Discovery + MCP Server

`agora-code serve` scans a codebase and exposes every discovered endpoint as an MCP tool, so your AI assistant can call your API directly.

### Route discovery pipeline

agora-code uses a 4-tier fallback pipeline:

```
Tier 1: OpenAPI/Swagger spec
        → Reads spec JSON/YAML directly
        → Activates for: URLs or .json/.yaml files
        → Accuracy: 100%

Tier 2: Python AST parser
        → Reads FastAPI/Flask/Django source code
        → Activates for: directories with .py files
        → Accuracy: ~95%

Tier 3: LLM extraction  ← auto-activates if Tier 1+2 find < 2 routes
        → Sends source to Claude/GPT/Gemini
        → Activates for: any language if API key set
        → Accuracy: ~90%

Tier 4: Regex fallback
        → Pattern-matches app.get(), router.post(), etc.
        → Always available as last resort
        → Accuracy: ~70%
```

### MCP server setup

```json
{
  "mcpServers": {
    "my-api": {
      "command": "agora-code",
      "args": ["serve", "/path/to/project", "--url", "http://localhost:7755"]
    }
  }
}
```

With authentication:
```bash
agora-code serve ./my-api --url http://localhost:7755 --auth-token mytoken
# or
AGORA_AUTH_TOKEN=mytoken agora-code serve ./my-api --url http://localhost:7755
```

---

## Workflow Builder

`agentify` detects multi-step workflows from your API routes using an LLM and generates executable Python code.

```bash
agora-code agentify ./my-api --show-mermaid
agora-code agentify ./my-api --output ./workflows
```

Example output for an e-commerce API:
```
✅ 1 workflow(s) detected:

  ◆  purchase_workflow
     Search for products, add to cart, and checkout
     Steps: GET /products/search → POST /cart/add → POST /cart/checkout
     Triggers: buy, order, purchase, shop
```

The `--output` flag generates ready-to-run Python files using the Agora AsyncFlow framework.

---

## Storage Architecture

```
.agora-code/
  session.json          Active session (project-local, gitignored)

~/.agora-code/
  memory.db             SQLite database (global, all projects)
    ├── sessions         Archived session records
    │     session_id, goal, hypothesis, branch, commit_sha,
    │     ticket, status, session_data (full JSON), tags
    ├── learnings        Permanent findings
    │     finding, evidence, confidence, tags, branch, files,
    │     namespace (personal/team), embedding (vector)
    ├── file_changes     Per-file git diff history
    │     file_path, diff_summary, diff_snippet, commit_sha,
    │     session_id, branch, timestamp
    └── api_calls        API call log (for serve/chat mode)
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
| `detail` | + all discoveries, decisions, blockers, files | ~500 |
| `full` | Raw JSON (no compression) | 3,000+ |

`inject` auto-picks the richest level that fits within `--token-budget` (default: 2,000 tokens).

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Enables Claude for LLM scan + workflow detection | — |
| `OPENAI_API_KEY` | Enables GPT + OpenAI embeddings for semantic recall | — |
| `GEMINI_API_KEY` | Enables Gemini for LLM scan + embeddings | — |
| `AGORA_CODE_DB` | Override memory DB path | `~/.agora-code/memory.db` |
| `AGORA_AUTH_TOKEN` | Default bearer token for API calls | — |
| `LLM_PROVIDER` | Force a provider: `claude` / `openai` / `gemini` | auto |
| `LLM_MODEL` | Override default model | provider default |

**Embeddings fallback chain**: OpenAI → Gemini → sentence-transformers (local, no API key needed) → keyword-only FTS5. Semantic search works offline if `sentence-transformers` is installed.

---

## Project Structure

```
agora-code/
├── agora_code/
│   ├── memory_server.py    MCP server for session memory (10 tools)
│   ├── session.py          Session lifecycle, git helpers, compression
│   ├── vector_store.py     SQLite + sqlite-vec + FTS5 + file_changes
│   ├── tldr.py             Context compression (index/summary/detail/full)
│   ├── embeddings.py       OpenAI/Gemini/sentence-transformers with LRU cache
│   ├── cli.py              All CLI commands
│   ├── agent.py            MCP server for API routes (JSON-RPC 2.0 over stdio)
│   ├── scanner.py          4-tier route discovery pipeline
│   ├── workflows.py        Workflow detection + Agora AsyncFlow code generator
│   ├── models.py           Route, Param, RouteCatalog dataclasses
│   └── extractors/
│       ├── openapi.py      Tier 1: OpenAPI/Swagger spec parser
│       ├── python_ast.py   Tier 2: FastAPI/Flask/Django AST
│       ├── llm.py          Tier 3: LLM extraction (Claude/GPT/Gemini)
│       └── regex.py        Tier 4: Regex fallback
├── .claude/hooks.json      Claude Code hook config
├── .gemini/settings.json   Gemini CLI hook config
├── .cursor/hooks.json      Cursor hook config
├── .github/hooks/          Copilot CLI hook config
├── SKILL.md                Agent tool usage reference
├── tests/                  60 tests — run with: pytest tests/
└── pyproject.toml
```

---

## What agora-code is NOT

- Not a hosted service — everything runs locally
- Not an API proxy or gateway
- Not a replacement for Postman (this is for AI-assisted development)
- Not cloud-dependent — works fully offline with keyword search only
- Not specific to Python — the memory layer works for any project; API scanning targets Python/OpenAPI

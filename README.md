# agora-code

**Persistent memory layer and API scanner for AI coding agents.**

agora-code does two things:

1. **Persistent session memory** — your AI assistant always knows where you left off, what you discovered, and what changed. Survives context window resets, new conversations, and multiple agents working in parallel.
2. **API discovery + MCP server** — scans any codebase (Python, OpenAPI spec) and exposes every endpoint as an MCP tool so an AI can call your API directly.

Works with **Claude Code**, **Cursor**, **Gemini CLI**, **Copilot CLI**, **Cline**, and any MCP-compatible assistant.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Installation](#installation)
- [Claude Code Plugin — One-Command Setup](#claude-code-plugin--one-command-setup)
- [Manual Hook Setup by Agent](#manual-hook-setup-by-agent)
- [Session Lifecycle](#session-lifecycle)
- [MCP Tools Reference](#mcp-tools-reference)
- [CLI Reference](#cli-reference)
- [Embeddings — Semantic Search](#embeddings--semantic-search)
- [Project Scoping](#project-scoping)
- [File Change Tracking](#file-change-tracking)
- [Git Integration](#git-integration)
- [Team Namespaces](#team-namespaces)
- [API Discovery + MCP Server](#api-discovery--mcp-server)
- [Storage Architecture](#storage-architecture)
- [Compression Levels](#compression-levels)
- [Environment Variables](#environment-variables)

---

## How It Works

The core problem: AI coding assistants forget everything between sessions. You spend an hour figuring out that a certain endpoint rejects `+` in emails, or that a particular middleware is causing a bug — and next session, you explain it all over again.

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
│  Scoped per project via git remote URL.                  │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Semantic / keyword search                      │
│  sqlite-vec for vector similarity (optional).            │
│  FTS5/BM25 keyword search — always works, zero config.   │
│  Local embeddings via sentence-transformers (offline).   │
└─────────────────────────────────────────────────────────┘
```

### Claude Code-specific features (this fork)

This fork extends agora-code with deeper Claude Code integration via three additional hooks:

**Per-prompt recall** (`UserPromptSubmit`): On every user message, agora-code searches stored learnings for anything relevant and injects matches as context — before Claude even starts thinking.

**Auto-goal** (`UserPromptSubmit`): If the session has no goal yet and the prompt is substantive (not a greeting or one-liner), the prompt is automatically saved as the session goal. No manual checkpoint needed.

**Conversation digest** (`Stop`/`SessionEnd`): When Claude finishes responding, the full conversation transcript (JSONL) is parsed to infer the real session goal, extract Claude's key findings, and store them as a searchable learning for future sessions.

```
User submits prompt
        ↓
UserPromptSubmit hook fires → on-prompt.sh:
  1. Recall relevant learnings → appended as context
  2. If no goal set → use prompt as goal
        ↓
Claude works
        ↓
Stop hook fires → on-stop.sh:
  1. Checkpoint current state
  2. Parse JSONL transcript → infer goal + extract findings
  3. Store conversation summary as searchable learning
        ↓
Next session: inject surfaces all of this automatically
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
# e.g. /usr/local/bin/agora-code
```

---

## Claude Code Plugin — One-Command Setup

The easiest way to use agora-code with Claude Code. Install once and it works in every project — no `CLAUDE.md` edits, no manual hook setup.

```bash
# 1. Install the CLI
pip install git+https://github.com/thebnbrkr/agora-code

# 2. Install the Claude Code plugin (user scope = all projects)
claude plugin install https://github.com/thebnbrkr/agora-code --scope user
```

After this, every Claude Code session automatically:
- Runs `agora-code inject` at startup to load your last session state
- Searches past learnings on every prompt and injects relevant ones as context
- Checkpoints before context compaction so nothing is lost
- Digests the conversation on stop to extract goals and findings

The plugin ships: a `SessionStart` hook, a `PreCompact` hook, a `PostToolUse` hook for file tracking, and a skill that teaches Claude the full tool reference.

> **Note:** For the deeper per-prompt recall and conversation digest features (the `UserPromptSubmit` and `Stop` hooks), use the manual Claude Code setup below — those hooks are not yet bundled in the plugin.

---

## Manual Hook Setup by Agent

### Claude Code

**One-command setup:**

```bash
agora-code install-hooks --claude-code
```

This generates `.claude/hooks.json` and the required hook scripts with correct paths for your machine. Restart Claude Code after running.

**Or manually** — create `.claude/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {"matcher": "", "hooks": [{"type": "command", "command": "agora-code inject --quiet 2>/dev/null || true"}]}
    ],
    "UserPromptSubmit": [
      {"matcher": "", "hooks": [{"type": "command", "command": ".claude/hooks/on-prompt.sh"}]}
    ],
    "PreToolUse": [
      {"matcher": "Read", "hooks": [{"type": "command", "command": ".claude/hooks/pre-read.sh"}]}
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {"type": "command", "command": "agora-code scan . --cache --quiet 2>/dev/null || true"},
          {"type": "command", "command": "python3 -c \"import sys,json,subprocess; d=json.loads(sys.stdin.read()); fp=(d.get('tool_input') or {}).get('file_path',''); subprocess.run(['agora-code','track-diff',fp]) if fp else None\" 2>/dev/null || true"}
        ]
      }
    ],
    "PreCompact": [
      {"matcher": "", "hooks": [{"type": "command", "command": "agora-code checkpoint --quiet 2>/dev/null || true"}]}
    ],
    "Stop": [
      {"matcher": "", "hooks": [{"type": "command", "command": ".claude/hooks/on-stop.sh"}]}
    ],
    "SessionEnd": [
      {"matcher": "", "hooks": [{"type": "command", "command": ".claude/hooks/on-stop.sh"}]}
    ]
  }
}
```

Also add a `CLAUDE.md` at the project root to tell Claude when to use each tool. Copy the included `CLAUDE.md` as a template.

> **Note:** `$CLAUDE_TOOL_INPUT_FILE_PATH` does not exist in Claude Code. File paths must be parsed from stdin JSON as shown above.

### Cursor

Create `.cursor/hooks.json`:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [{"command": ".cursor/hooks/session-start.sh"}],
    "afterFileEdit": [{"command": ".cursor/hooks/after-file-edit.sh"}],
    "preCompact": [{"command": ".cursor/hooks/pre-compact.sh"}]
  }
}
```

Create `.cursor/hooks/session-start.sh`:

```sh
#!/bin/sh
cat > /dev/null
context=$(agora-code inject --quiet 2>/dev/null)
if [ -n "$context" ]; then
    escaped=$(printf '%s' "$context" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
    printf '{"additional_context":%s}\n' "$escaped"
else
    printf '{}\n'
fi
```

Create `.cursor/hooks/after-file-edit.sh`:

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

Create `.cursor/hooks/pre-compact.sh`:

```sh
#!/bin/sh
cat > /dev/null
agora-code checkpoint --quiet
exit 0
```

Make scripts executable: `chmod +x .cursor/hooks/*.sh`

> **Important:** Cursor requires `"version": 1` at the top of `hooks.json` and camelCase event names (`sessionStart`, `afterFileEdit`, `preCompact`). Hook scripts must be separate files — inline commands are not supported.

### Gemini CLI

Create `.gemini/settings.json`:

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

---

## Session Lifecycle

A **session** is a goal-oriented work period. It spans context window resets, new conversations, and agent restarts — until you explicitly call `complete`.

```
Start of work
      │
      ▼
SessionStart hook → agora-code inject:
  1. Reads .agora-code/session.json (active session)
  2. If session is new → queries DB for last session + top learnings
  3. Returns ~200-500 token summary into Claude's context
      │
      ▼
UserPromptSubmit hook → on-prompt.sh:
  1. Searches learnings for anything relevant to the prompt
  2. Injects matches as context before Claude responds
  3. Auto-sets goal from first substantive prompt
      │
      ▼
[work happens — context window fills]
      │
      ▼
PreCompact hook → agora-code checkpoint --quiet
  Saves state before Claude compresses the window
      │
      ▼
[Claude finishes responding]
      │
      ▼
Stop hook → on-stop.sh:
  1. Checkpoint
  2. Parse conversation transcript (JSONL)
  3. Infer real goal from user messages
  4. Extract Claude's key findings
  5. Store as searchable learning
      │
      ▼
[task complete — call explicitly]
      │
      ▼
agora-code complete --summary "Fixed the 422 bug — email regex was too strict"
  Archives session to memory.db with embedding for future recall
```

---

## MCP Tools Reference

The memory server exposes tools to any MCP-compatible AI assistant via `agora-code memory-server`.

### `get_session_context`

Returns compressed session state. At session start, auto-populates from DB if the session is new.

```
level: "index" | "summary" | "detail" | "full"  (default: "detail")
```

### `save_checkpoint`

Saves current state to `session.json` and the DB.

```
goal:          string  — what you're trying to accomplish
hypothesis:    string  — current working theory
action:        string  — what you're doing right now
context:       string  — free-text notes
files_changed: array   — e.g. ["auth.py:added retry logic"]
next_steps:    array
blockers:      array
```

Auto-captured on every call: git branch, HEAD SHA, ticket number from branch name, uncommitted files.

### `store_learning`

Stores a permanent finding. Persists across sessions. Embeddings stored for semantic recall.

```
finding:    string  (required)
evidence:   string
confidence: "confirmed" | "likely" | "hypothesis"  (default: "confirmed")
tags:       array
```

### `recall_learnings`

Searches past findings. Semantic search if embeddings are configured, FTS5 otherwise. Results reranked by relevance + recency (48h half-life) + confidence + branch/file overlap.

```
query: string  (required)
limit: integer  (default: 5)
```

### `complete_session`

Archives the session to long-term storage with an embedding for future recall.

```
summary: string  (required)
outcome: "success" | "partial" | "abandoned"  (default: "success")
```

### `recall_file_history`

Returns compact change history for a file — what changed, when, in which session, on which branch.

```
file_path: string  (required)
limit:     integer  (default: 10)
```

### Other tools

- `get_memory_stats` — session count, learning count, search mode, DB location
- `list_sessions` — list past sessions, filterable by branch
- `store_team_learning` — same as `store_learning` but in the shared `team` namespace
- `recall_team` — search the shared team knowledge base

---

## Connect via MCP

Add the memory server to your MCP config once — it works across all projects.

> Use the full binary path (`which agora-code`). Most editors don't inherit your shell PATH when spawning MCP processes.

### Claude Code

```bash
claude mcp add agora-memory -- /full/path/to/agora-code memory-server
```

Or edit `~/.claude.json`:

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

### Cursor

**Settings → MCP → Edit in settings.json:**

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

### Virtualenv

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

---

## CLI Reference

### Session management

```bash
# Show current session + DB stats
agora-code status

# Load and print session context (used by hooks)
agora-code inject
agora-code inject --level detail
agora-code inject --token-budget 1000   # auto-pick level to fit budget
agora-code inject --raw                 # full session JSON

# Save session state
agora-code checkpoint \
  --goal "Fix POST /users 422 errors" \
  --hypothesis "Email regex too strict" \
  --action "Testing edge cases" \
  --file "auth.py:added retry" \
  --next "Write integration test" \
  --blocker "Waiting for staging deploy"

# Archive completed session
agora-code complete --summary "Fixed 422, deployed to staging" --outcome success

# List past sessions
agora-code restore

# Restore a past session as active
agora-code restore <session_id>
```

### Knowledge base

```bash
# Store a permanent learning
agora-code learn "POST /users rejects + in email addresses" \
  --confidence confirmed \
  --tags api,validation

# Search learnings
agora-code recall "<query>"
agora-code recall "cursor hooks" --limit 10
```

### File tracking

```bash
# View per-file change history
agora-code file-history agora_code/auth.py

# Manually capture a file's git diff
agora-code track-diff agora_code/auth.py
agora-code track-diff agora_code/auth.py --committed  # vs last commit
```

### API scanning

```bash
agora-code scan <target>           # scan codebase or OpenAPI spec
agora-code serve <target> --url http://localhost:7755   # start API MCP server
agora-code stats <target>          # show API call stats
agora-code chat <target> --url http://localhost:7755    # interactive chat
agora-code agentify <target>       # detect workflows, generate code
```

---

## Embeddings — Semantic Search

agora-code works without embeddings (FTS5 keyword search always works), but embeddings make recall significantly better.

### Provider priority (auto-detected)

```
EMBEDDING_PROVIDER=auto (default):
  1. OpenAI  text-embedding-3-small  (1536 dims) — set OPENAI_API_KEY
  2. Gemini  gemini-embedding-001    (768 dims)  — set GEMINI_API_KEY
  3. Local   BAAI/bge-small-en-v1.5  (384 dims)  — install sentence-transformers
  4. None    → FTS5 keyword search only
```

### Force a specific provider

```bash
# Fully offline — no API key, no internet
export EMBEDDING_PROVIDER=local
pip install "git+https://github.com/thebnbrkr/agora-code[local]"

# Override the local model
export LOCAL_EMBEDDING_MODEL=BAAI/bge-large-en-v1.5  # 1024 dims, more accurate
```

### Check which provider is active

```bash
agora-code status
# Shows: [vector search: on (openai)] or [vector search: off (install sqlite-vec)]
```

---

## Project Scoping

agora-code scopes all sessions and learnings per project to prevent cross-project bleed.

**`project_id` is derived from your git remote URL:**

```bash
git remote get-url origin
# → https://github.com/you/your-project  (used as project_id)
```

Falls back to the current directory name if no git remote is set.

---

## File Change Tracking

Every time the AI edits a file, agora-code captures what changed and stores a compact summary. This builds a per-file history queryable without reading the file.

```bash
agora-code file-history agora_code/auth.py

# Change history for agora_code/auth.py (3 entries):
# • 2026-03-14 [main] @abc123def456: added _get_project_id(), updated update_session
# • 2026-03-12 [main]: added retry logic to validate(), updated imports
# • 2026-03-10 [feat/auth]: initial auth implementation
```

The `PostToolUse` hook runs `track-diff` automatically on every file write.

---

## Git Integration

agora-code captures git context on every checkpoint automatically.

| Field | How detected | Example |
|---|---|---|
| `branch` | `git rev-parse --abbrev-ref HEAD` | `feat/auth-service` |
| `commit_sha` | `git rev-parse --short=12 HEAD` | `abc123def456` |
| `ticket` | Regex on branch name | `JIRA-423` from `JIRA-423-fix-login` |
| `uncommitted_files` | `git status --porcelain` | `["auth.py", "middleware.py"]` |
| `project_id` | `git remote get-url origin` | `https://github.com/you/repo` |
| `goal` (fallback) | Derived from branch | `"JIRA-423: fix login"` |

---

## Team Namespaces

Multiple agents on the same project can share a knowledge pool:

```
Agent A: agora-code learn "Rate limit on /auth: 100 req/min per IP" --tags team
Agent B: agora-code recall "rate limiting"  → finds Agent A's learning
```

For shared memory, all agents must point to the same DB:

```bash
export AGORA_CODE_DB=/shared/path/agora-memory.db
```

---

## API Discovery + MCP Server

`agora-code serve` scans a codebase and exposes every discovered endpoint as an MCP tool.

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
```

---

## Storage Architecture

```
.agora-code/
  session.json          Active session (project-local, gitignored)
  .gitignore            Auto-created

~/.agora-code/
  memory.db             SQLite database (global — all projects)
    ├── sessions         Archived session records
    ├── learnings        Permanent findings + embeddings
    ├── file_changes     Per-file git diff history
    └── api_calls        HTTP interaction log (serve/chat mode)
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
| `full` | Raw JSON | 3,000+ |

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

---

## What agora-code is NOT

- Not a hosted service — everything runs locally (SQLite, local files)
- Not cloud-dependent — works fully offline with FTS5 keyword search
- Not an API proxy or gateway
- Not a replacement for Postman
- Not specific to Python — the memory layer works for any project; API scanning targets Python/OpenAPI

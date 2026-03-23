# agora-code

Persistent memory layer for AI coding agents. Survives context window resets, new conversations, and agent restarts.

---

## What it does

AI coding assistants forget everything between sessions. You spend time figuring out that a certain endpoint rejects `+` in emails, or that a particular middleware is causing a bug — and next session, you explain it all over again.

agora-code fixes this by automatically:
- Loading your last session state at the start of every conversation
- Injecting relevant past findings into context on every prompt
- Indexing symbols and diffs every time you read or edit a file
- Parsing the conversation transcript on stop to extract goals and findings

---

## Install

### Claude Code plugin (recommended)

```
/plugin marketplace add thebnbrkr/agora-code
/plugin install agora-code@thebnbrkr/agora-code
```

Restart Claude Code. No further setup needed — hooks wire up automatically.

**Verify:**
```bash
agora-code status        # current session + DB stats
agora-code status -p     # scoped to this repo only
```

### pip (manual)

```bash
pip install git+https://github.com/thebnbrkr/agora-code.git
```

Then run once inside your project and restart your editor:

```bash
cd your-project
agora-code install-hooks --claude-code
```

Optional extras:

```bash
pip install "git+https://github.com/thebnbrkr/agora-code[local]"    # local embeddings, offline
pip install "git+https://github.com/thebnbrkr/agora-code[openai]"   # OpenAI embeddings
pip install "git+https://github.com/thebnbrkr/agora-code[gemini]"   # Gemini embeddings
pip install "git+https://github.com/thebnbrkr/agora-code[all]"      # everything
```

### Cursor / other editors

**Step 1 — Hooks** (session inject + file tracking):

```bash
mkdir -p .cursor/hooks
cp /path/to/agora-code/.cursor/hooks.json .cursor/
cp /path/to/agora-code/.cursor/hooks/*.sh .cursor/hooks/
chmod +x .cursor/hooks/*.sh
```

Restart Cursor.

**Step 2 — MCP** (so the AI can call memory tools directly):

Settings → MCP → Edit in settings.json:

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

Use `which agora-code` to get the full path. Restart Cursor.

> **Note:** Gemini CLI and GitHub Copilot integrations are currently a work in progress. Hook support for those editors is planned — see [FUTURE_HOOKS.md](FUTURE_HOOKS.md) for the roadmap.

---

## How it works

Memory is stored in three layers:

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

### Session lifecycle

```
Start     →  SessionStart hook injects last checkpoint + top learnings
Prompt    →  on-prompt.sh recalls relevant learnings, sets goal
Working   →  on-read / on-edit index symbols and diffs as you go
Compact   →  PreCompact hook checkpoints before context window compresses
Stop      →  on-stop.sh parses transcript → structured checkpoint in DB
Done      →  agora-code complete --summary "..." archives to long-term memory
```

### Claude Code hooks

| Hook | Event | Does |
|---|---|---|
| `pre-read.sh` | PreToolUse(Read) | Intercepts large files — auto-summarizes before Claude reads |
| `on-read.sh` | PostToolUse(Read) | Indexes symbols + code blocks into DB |
| `on-grep.sh` | PostToolUse(Grep) | Indexes files matched by grep |
| `on-edit.sh` | PostToolUse(Write/Edit) | Re-indexes symbols, tracks diff |
| `on-bash.sh` | PostToolUse(Bash) | Tags committed files with SHA on `git commit` |
| `on-prompt.sh` | UserPromptSubmit | Auto-sets goal, recalls relevant learnings |
| `on-stop.sh` | Stop | Parses transcript → structured checkpoint |

---

## CLI Reference

### Session management

#### `agora-code inject`

Print compressed session context for injection into any coding agent. Used automatically by the SessionStart hook — run manually to see what gets injected.

```bash
agora-code inject                   # auto-picks compression level
agora-code inject --level detail    # more verbose output
agora-code inject --raw             # print raw session JSON
agora-code inject --quiet           # exit silently if no session (for hooks)
```

| Option | Description |
|---|---|
| `--level` | `index` / `summary` / `detail` / `full` — compression level |
| `--token-budget` | Max tokens for auto-level selection |
| `--raw` | Print raw session JSON instead of formatted output |
| `--quiet` | Exit silently if no session exists (safe for hook use) |

---

#### `agora-code checkpoint`

Save the current session state to `.agora-code/session.json`. Call this after completing any meaningful step.

```bash
agora-code checkpoint --goal "Refactor auth module"
agora-code checkpoint --hypothesis "SessionManager needs a lock"
agora-code checkpoint --action "Adding retry logic to validate()"
agora-code checkpoint --file "auth.py:added retry" --file "tests/test_auth.py:updated tests"
agora-code checkpoint --next "Write test for edge case" --blocker "Waiting on review"
```

| Option | Description |
|---|---|
| `--goal` | What you're trying to accomplish |
| `--hypothesis` | Current working theory |
| `--action` | What you're doing right now |
| `--context` | Free-text project notes |
| `--api` | Base URL of the API being tested |
| `--next` | Next step (repeatable) |
| `--blocker` | Blocker (repeatable) |

---

#### `agora-code complete`

Archive the current session to long-term memory. Call when you're done with a task.

```bash
agora-code complete --summary "Refactored auth, added retry logic"
agora-code complete --summary "Partial progress on rate limiting" --outcome partial
```

| Option | Description |
|---|---|
| `--summary` | What you accomplished |
| `--outcome` | `success` / `partial` / `abandoned` |

---

#### `agora-code restore`

List or restore a past session as the active session.

```bash
agora-code restore                                  # list available sessions
agora-code restore 2026-03-08-debug-post-users      # restore specific session
```

---

#### `agora-code status`

Show the current session state and DB statistics.

```bash
agora-code status           # global counts
agora-code status -p        # scoped to the current repo
```

| Option | Description |
|---|---|
| `-p` / `--project` | Scope counts to the current repo only |

---

### Memory & learnings

#### `agora-code learn`

Store a permanent finding that will be recalled in future sessions.

```bash
agora-code learn "POST /users rejects + in emails" --tags email,validation
agora-code learn "Rate limit is 100 req/min" --endpoint "GET /data" --confidence confirmed
```

| Option | Description |
|---|---|
| `--endpoint` | e.g. `POST /users` |
| `--api` | Base URL of the API |
| `--evidence` | Supporting evidence or example |
| `--confidence` | `confirmed` / `likely` / `hypothesis` |
| `--tags` | Comma-separated tags |

---

#### `agora-code recall`

Search your learnings knowledge base. Uses semantic search with embeddings if configured, otherwise BM25 keyword search.

```bash
agora-code recall "email validation"
agora-code recall "rate limit" --limit 10
agora-code recall                            # show most recent learnings
```

| Option | Description |
|---|---|
| `-n` / `--limit` | Max results to return |

---

#### `agora-code remove`

Delete a learning by ID, scoped to the current repo.

```bash
agora-code remove abc12345
```

---

#### `agora-code memory`

Show DB path, row counts, and a short dump of recent sessions and learnings.

```bash
agora-code memory
agora-code memory 20              # show 20 entries
agora-code memory --verbose       # include stored AST summaries and code blocks
```

| Option | Description |
|---|---|
| `-n` / `--limit` | Max sessions and learnings to show (default 10) |
| `-v` / `--verbose` | Print stored AST summaries and code blocks |

---

### Files & symbols

#### `agora-code summarize`

Summarize a file's structure for token-efficient context injection. Uses a cached AST from the DB when available; otherwise reads from disk. Files under the line threshold are passed through unmodified.

```bash
agora-code summarize agora_code/session.py
agora-code summarize package.json --json-output     # for hook consumption
agora-code summarize large_file.py --threshold 50   # lower threshold
```

| Option | Description |
|---|---|
| `--max-tokens` | Token budget for the summary |
| `--json-output` | Output JSON (used by pre-read hook) |
| `--threshold` | Line count below which the file passes through unmodified |

---

#### `agora-code index`

Re-index a file into the DB (updates `symbol_notes` and `file_snapshots`). Called automatically by `on-edit.sh` — run manually if the DB is out of sync.

```bash
agora-code index agora_code/auth.py
```

---

#### `agora-code file-history`

Show the tracked change history for a specific file.

```bash
agora-code file-history agora_code/auth.py
agora-code file-history agora_code/session.py --limit 5
```

| Option | Description |
|---|---|
| `-n` / `--limit` | Max entries to show |

---

#### `agora-code track-diff`

Capture a git diff for a file and store a compact summary in memory. Called automatically by hooks — run manually to force a snapshot.

```bash
agora-code track-diff agora_code/auth.py
agora-code track-diff --all               # all uncommitted files
agora-code track-diff auth.py --committed # diff against HEAD~1
```

| Option | Description |
|---|---|
| `--all` | Track all uncommitted (staged + unstaged) files |
| `--committed` | Diff against HEAD~1 instead of working tree |

---

### Listing commands

Quick inspection of what's stored in the DB, without needing SQL.

```bash
agora-code list-sessions        # archived session records
agora-code list-learnings       # permanent findings
agora-code list-snapshots       # AST summaries per file
agora-code list-symbols         # indexed functions and classes
agora-code list-file-changes    # per-file diff history
agora-code list-api-calls       # HTTP calls from serve/chat
```

All listing commands accept `-n` / `--limit` to cap the number of results. `list-symbols` also accepts `--file <path>` to filter by file.

---

### Setup

#### `agora-code install-hooks`

Install hooks to auto-track file changes.

```bash
agora-code install-hooks                    # git post-commit hook
agora-code install-hooks --claude-code      # Claude Code hooks
agora-code install-hooks --claude-code --force  # overwrite existing
```

| Option | Description |
|---|---|
| `--force` | Overwrite existing hooks |
| `--claude-code` | Install Claude Code hooks (`.claude/hooks.json` + shell scripts) |

---

### API tools

These commands are for working with HTTP APIs — scanning routes, running an MCP server for your API, and calling it in natural language.

#### `agora-code scan`

Discover all API routes in a codebase or from a live URL.

```bash
agora-code scan ./my-fastapi-app
agora-code scan https://api.example.com
agora-code scan ./my-app --output routes.json
agora-code scan ./node-app --use-llm
agora-code scan . --cache --quiet           # hook-safe, uses cached routes
```

| Option | Description |
|---|---|
| `-o` / `--output` | Save discovered routes to a JSON file |
| `--use-llm` | Enable LLM-assisted extraction (costs tokens) |
| `--llm-provider` | `openai` or `gemini` |
| `--format` | `table` / `json` / `mcp` |
| `--cache` | Use cached `discovered_routes.json` if present |

---

#### `agora-code serve`

Start an MCP server for your API. Plug into Claude Desktop or Cursor so your AI can call your API directly.

```bash
agora-code serve ./my-api --url http://localhost:8000
agora-code serve https://api.example.com --url https://api.example.com
```

Add to Claude Desktop config:

```json
{
  "mcpServers": {
    "my-api": {
      "command": "agora-code",
      "args": ["serve", "./my-api", "--url", "http://localhost:8000"]
    }
  }
}
```

| Option | Description |
|---|---|
| `-u` / `--url` | Base URL of the live API (required) |

---

#### `agora-code chat`

Start an interactive natural-language chat session against your API.

```bash
agora-code chat ./my-api --url http://localhost:8000
agora-code chat https://api.example.com --url https://api.example.com --level index
```

| Option | Description |
|---|---|
| `-u` / `--url` | Base URL of the live API (required) |
| `--level` | `index` / `summary` / `detail` / `full` — context compression level |
| `--use-llm` | Enable LLM route extraction |
| `--auth-token` | Bearer token or API key |
| `--auth-type` | `bearer` / `api-key` / `basic` / `none` |

---

#### `agora-code auth`

Configure authentication for API calls.

```bash
agora-code auth ./my-api
agora-code auth ./my-api --type bearer --token mytoken123
```

| Option | Description |
|---|---|
| `--type` | `bearer` / `api-key` / `basic` / `none` |
| `--token` | Token value (skips prompt if provided) |

---

#### `agora-code stats`

Show API call statistics and patterns from memory.

```bash
agora-code stats ./my-api
agora-code stats ./my-api --window 48
```

| Option | Description |
|---|---|
| `--window` | Time window in hours for pattern detection |

---

#### `agora-code agentify`

Scan a repo and auto-generate workflow code from its API routes.

```bash
agora-code agentify ./my-api
agora-code agentify ./my-api --output ./workflows --show-mermaid
agora-code agentify https://api.example.com --llm-provider claude
```

| Option | Description |
|---|---|
| `--llm-provider` | `auto` / `claude` / `openai` / `gemini` |
| `--llm-model` | Override the default model |
| `-o` / `--output` | Directory to write generated flow code |
| `--show-mermaid` | Print a Mermaid DAG diagram |

---

#### `agora-code memory-server`

Start a project-agnostic MCP server for day-to-day coding memory. Exposes session and learning tools to any MCP-compatible editor.

```bash
agora-code memory-server
```

Add to your editor's MCP config:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "agora-code",
      "args": ["memory-server"]
    }
  }
}
```

---

## MCP Tools Reference

| Tool | When to use |
|---|---|
| `get_session_context` | Start of every chat — loads last checkpoint, recent learnings, git state |
| `save_checkpoint` | After completing a meaningful step |
| `store_learning` | Non-obvious finding: bug, gotcha, architectural decision |
| `recall_learnings` | Before starting something — check if it was solved before |
| `get_file_symbols` | All indexed functions/classes for a file with line numbers |
| `search_symbols` | Search across all indexed symbols by name or description |
| `recall_file_history` | See what changed in a file across past sessions |
| `complete_session` | Archive session to long-term memory when done |
| `list_sessions` | Find past sessions |
| `get_memory_stats` | DB usage stats |

---

## Embeddings

Works without embeddings (FTS5 keyword search always works). With embeddings, recall is significantly better.

```
EMBEDDING_PROVIDER=auto (default):
  1. OpenAI  text-embedding-3-small  — set OPENAI_API_KEY
  2. Gemini  gemini-embedding-001    — set GEMINI_API_KEY
  3. Local   BAAI/bge-small-en-v1.5  — pip install agora-code[local]
  4. None    → FTS5 keyword search only
```

---

## Storage

```
.agora-code/
  session.json          Active session (project-local, gitignored)

~/.agora-code/
  memory.db             SQLite database (global — all projects)
    ├── sessions         Archived session records
    ├── learnings        Permanent findings (FTS5 + optional vec)
    ├── file_changes     Per-file git diff history
    ├── file_snapshots   AST summary per (project, file, branch)
    ├── symbol_notes     Per-symbol signature + code_block
    └── api_calls        HTTP interaction log
```

Override DB path:

```bash
export AGORA_CODE_DB=/path/to/custom/memory.db
```

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI embeddings + GPT LLM scan | — |
| `GEMINI_API_KEY` | Gemini embeddings + LLM scan | — |
| `ANTHROPIC_API_KEY` | Claude for LLM scan + workflow detection | — |
| `EMBEDDING_PROVIDER` | `auto` / `openai` / `gemini` / `local` | `auto` |
| `LOCAL_EMBEDDING_MODEL` | sentence-transformers model name | `BAAI/bge-small-en-v1.5` |
| `AGORA_CODE_DB` | Override memory DB path | `~/.agora-code/memory.db` |
| `AGORA_AUTH_TOKEN` | Default bearer token for API calls | — |

---

## Project Scoping

All sessions and learnings are scoped per project via git remote URL:

```bash
git remote get-url origin
# → https://github.com/you/your-project  (used as project_id)
```

Falls back to directory name if no git remote is set.

---

## Troubleshooting

### Testing the pre-read hook manually

The pre-read hook intercepts large file reads and serves a summary instead. To test it, open a **new terminal**, navigate to your project root, and run:

```bash
echo '{"file_path": "/path/to/your/large-file.py"}' | bash .claude/hooks/pre-read.sh
echo "exit code: $?"
```

**What you should see:**
- The file summary printed to stdout
- `exit code: 2` — meaning the hook blocked the read and served the summary instead
- If the file is under the 100-line threshold, no output and `exit code: 0` (pass through)

**If the hook fails silently**, check the error log:

```bash
cat /tmp/agora-pre-read-error.log
```

This log is only created when something goes wrong inside the hook (e.g. `agora-code` not found in PATH, bad JSON output, Python parse failure). If the file doesn't exist, the hook ran without errors.

> **Important:** Run hook tests in a new terminal from the project root — not from inside Claude Code. The hooks run as shell scripts and need `agora-code` on your PATH.

---

### Common issues

**`agora-code` not found in hooks**

Hooks run in a non-interactive shell. If `agora-code` is installed in a virtualenv or via pyenv, the hook may not find it. Fix by using the full path in the hook scripts:

```bash
which agora-code   # get the full path
# then open .claude/hooks/pre-read.sh and replace `agora-code` with the full path
```

**No embeddings / semantic search not working**

```
⚠️ No embedding generated — set OPENAI_API_KEY for semantic recall.
   Keyword search will still work.
```

This is expected if you haven't set an API key. `recall` and `learn` fall back to BM25 keyword search automatically — everything still works, just with less fuzzy matching. To enable semantic search:

```bash
export OPENAI_API_KEY=sk-...   # or GEMINI_API_KEY / pip install agora-code[local]
```

**`agentify` fails with "No LLM provider"**

```
❌ No LLM provider for workflow detection.
```

Route scanning works without an API key. The LLM step (workflow detection) needs one:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # recommended
```

---

## Roadmap

The following integrations are planned but not yet available:

- **Gemini CLI hooks**: `BeforeAgent`, `AfterAgent`, `BeforeToolSelection`, `BeforeModel` hooks for Gemini CLI are in progress. These will enable per-turn context injection, response validation, and tool filtering.
- **GitHub Copilot**: hook support for GitHub Copilot is not yet available.
- **Cursor shell output summarization**: `afterShellExecution` hook to summarize large shell output (test runs, `git log`, `npm install`) the same way file reads are summarized.
- **Subagent awareness**: injecting session context into Claude subagents so they don't start blind.
- **Error memory**: `PostToolUseFailure` hook to track recurring errors and surface prior resolutions.

See [FUTURE_HOOKS.md](FUTURE_HOOKS.md) for the full roadmap and priority order.

# agora-code

Persistent memory layer for AI coding agents. Survives context window resets, new conversations, and agent restarts.

---

## Quick start

```bash
pip install git+https://github.com/thebnbrkr/agora-code.git
```

**Claude Code** — run this once inside any project, then restart Claude Code:

```bash
cd your-project
agora-code install-hooks --claude-code
```

That's it. From now on every Claude Code session in that project automatically:
- Loads your last session state on start
- Searches past learnings on every prompt and injects relevant ones as context
- Indexes symbols and diffs on every file read/edit
- Digests the conversation on stop to extract goals and findings

**Verify it's working:**

```bash
agora-code status        # current session + DB stats
agora-code status -p     # scoped to this repo only
```

---

## Cursor / other editors

**Step 1 — Hooks** (session inject + file tracking):

```bash
mkdir -p .cursor/hooks
cp /path/to/agora-code/.cursor/hooks.json .cursor/
cp /path/to/agora-code/.cursor/hooks/*.sh .cursor/hooks/
chmod +x .cursor/hooks/*.sh
# Restart Cursor
```

**Step 2 — MCP** (so the AI can call session/checkpoint/learn/recall):

Settings → MCP → Edit in settings.json:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "/full/path/from/which/agora-code/agora-code",
      "args": ["memory-server"]
    }
  }
}
```

Use `which agora-code` to get the full path. Restart Cursor.

---

## How it works

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

## Session lifecycle

```
Start of work
      │
      ▼
SessionStart hook → agora-code inject:
  Loads last checkpoint + top learnings into context
      │
      ▼
UserPromptSubmit hook → on-prompt.sh:
  Searches learnings relevant to the prompt → injects as context
  Auto-sets goal from first substantive prompt
      │
      ▼
PreCompact hook → agora-code checkpoint --quiet
  Saves state before Claude compresses the window
      │
      ▼
Stop hook → on-stop.sh:
  Checkpoint + parse transcript → store as searchable learning
      │
      ▼
[task complete — call explicitly]
agora-code complete --summary "Fixed the 422 bug"
```

---

## Installation

```bash
pip install git+https://github.com/thebnbrkr/agora-code
```

Optional extras:

```bash
pip install "git+https://github.com/thebnbrkr/agora-code[local]"    # local embeddings, offline
pip install "git+https://github.com/thebnbrkr/agora-code[openai]"   # OpenAI embeddings
pip install "git+https://github.com/thebnbrkr/agora-code[gemini]"   # Gemini embeddings
pip install "git+https://github.com/thebnbrkr/agora-code[all]"      # everything
```

---

## CLI Reference

| Command | Purpose |
|--------|--------|
| `agora-code status` | Current session + DB stats |
| `agora-code status -p` | Same, scoped to current repo only |
| `agora-code memory` [N] | Dump DB: sessions, learnings, snapshots, symbols |
| `agora-code inject` | Load session context (used by hooks) |
| `agora-code checkpoint` | Save goal, hypothesis, files changed, next steps |
| `agora-code complete` | Archive session with summary/outcome |
| `agora-code restore` [SESSION_ID] | List or restore a past session |
| `agora-code learn` | Store a permanent finding |
| `agora-code recall` "<query>" | Search learnings |
| `agora-code summarize <path>` | File structure summary (token-efficient) |
| `agora-code file-history <path>` | Per-file change history |
| `agora-code track-diff <path>` | Capture git diff for one file |
| `agora-code index <path>` | Re-index file into DB |
| `agora-code install-hooks --claude-code` | Generate .claude/settings.json + hooks |
| `agora-code memory-server` | Start MCP server (stdio) |
| `agora-code list-sessions` | List sessions (no SQL) |
| `agora-code list-learnings` | List learnings |
| `agora-code list-snapshots` | List file snapshots (AST) |
| `agora-code list-symbols` | List symbol notes |
| `agora-code list-file-changes` | List file changes |
| `agora-code scan <target>` | Discover API routes |
| `agora-code serve` | Start API MCP server |
| `agora-code stats` | API call stats |
| `agora-code chat` | Interactive API chat |
| `agora-code agentify` | Detect workflows, generate flow code |

---

## MCP Tools Reference

Add the memory server to any MCP-compatible editor:

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

## Embeddings — Semantic Search

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

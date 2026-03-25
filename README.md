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

### Step 1 — Install the package (once, globally)

```bash
pip install git+https://github.com/thebnbrkr/agora-code.git
```

This installs the `agora-code` command. You only ever do this once.

**Verify it worked:**
```bash
agora-code --version
```

> **macOS note:** If you get a permission error, use `pip install --user` instead. Then add the binary to your PATH:
> ```bash
> pip install --user git+https://github.com/thebnbrkr/agora-code.git
> export PATH="$(python3 -m site --user-base)/bin:$PATH"
> # add that export line to ~/.zshrc or ~/.bashrc to make it permanent
> ```

---

### Step 2 — Set up a project (once per repo)

Inside any project you want agora-code to track:

```bash
cd your-project
agora-code install-hooks --claude-code
```

This does four things:
1. Writes `.claude/settings.json` — wires up hooks so Claude Code fires them automatically
2. Writes `.claude/hooks/*.sh` — the hook scripts that run on read, edit, commit, stop
3. Installs `~/.claude/skills/agora-code/SKILL.md` globally — enables the `/agora-code` skill in all repos (only happens once)
4. Writes `.mcp.json` — registers the memory MCP server for this project

> If `.claude/settings.json` already exists in your project, use `--force` to overwrite:
> ```bash
> agora-code install-hooks --claude-code --force
> ```

**Restart Claude Code** after running this.

---

### Step 3 — Start your first session

Open Claude Code in your project. At the start of every session, type:

```
/agora-code
```

This loads the skill — it tells Claude the rules for how to use agora-code (when to inject context, when to summarize files, when to save progress). You need to do this once per session.

Then run:

```bash
agora-code inject
```

This loads your previous session context (last checkpoint, learnings, git state) into the conversation.

---

### That's it. Here's what happens automatically from now on:

| When you... | agora-code automatically... |
|---|---|
| Start a session | Injects last checkpoint + relevant learnings |
| Submit a prompt | Recalls relevant past findings |
| Read a large file | Summarizes it first (saves tokens) |
| Edit a file | Re-indexes symbols, tracks the diff |
| Run `git commit` | Stores learnings from the commit |
| End a session | Parses the transcript into a structured checkpoint |

---

### Optional: better recall with embeddings

By default, `recall` uses keyword search. For semantic (fuzzy) search:

```bash
# pick one:
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...
pip install "git+https://github.com/thebnbrkr/agora-code[local]"  # offline, no API key needed
```

---

### Cursor / Claude Desktop / other MCP editors

Install the package (same Step 1 above), then add to your editor's MCP config:

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

Use `which agora-code` to get the full path if your editor can't find the binary. Restart your editor.

> Hook-based auto-tracking (file indexing, transcript parsing) is Claude Code only. In Cursor and Claude Desktop, use the MCP tools directly: `get_session_context`, `save_checkpoint`, `store_learning`, `recall_learnings`.

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
│  FTS5/BM25 keyword search — always works, zero config.   │
│  Optional: vector similarity via embeddings.             │
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

## CLI Reference

### Session management

#### `agora-code inject`

Load your previous session context into the conversation. Run this at the start of every session (the SessionStart hook does it automatically, but you can run it manually too).

```bash
agora-code inject                   # auto-picks compression level
agora-code inject --level detail    # more verbose output
agora-code inject --raw             # print raw session JSON
agora-code inject --quiet           # exit silently if no session (for hooks)
```

---

#### `agora-code checkpoint`

Save current session state. Run after completing any meaningful step.

```bash
agora-code checkpoint --goal "Refactor auth module"
agora-code checkpoint --next "Write test for edge case" --blocker "Waiting on review"
```

| Option | Description |
|---|---|
| `--goal` | What you're trying to accomplish |
| `--hypothesis` | Current working theory |
| `--action` | What you're doing right now |
| `--context` | Free-text project notes |
| `--next` | Next step (repeatable) |
| `--blocker` | Blocker (repeatable) |

---

#### `agora-code complete`

Archive the session to long-term memory. Run when you're done with a task.

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

List or restore a past session.

```bash
agora-code restore                                  # list available sessions
agora-code restore 2026-03-08-debug-post-users      # restore specific session
```

---

#### `agora-code status`

Show current session state and DB statistics.

```bash
agora-code status           # global counts
agora-code status -p        # scoped to the current repo
```

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
| `--evidence` | Supporting evidence or example |
| `--confidence` | `confirmed` / `likely` / `hypothesis` |
| `--tags` | Comma-separated tags |

---

#### `agora-code recall`

Search your learnings knowledge base.

```bash
agora-code recall "email validation"
agora-code recall "rate limit" --limit 10
agora-code recall                            # show most recent
```

---

#### `agora-code remove`

Delete a learning by ID, scoped to the current repo.

```bash
agora-code remove abc12345
```

---

#### `agora-code memory`

Show DB path, row counts, and recent sessions and learnings.

```bash
agora-code memory
agora-code memory 20
agora-code memory --verbose
```

---

### Files & symbols

#### `agora-code summarize`

Summarize a file's structure for token-efficient context. Files under the threshold pass through unmodified.

```bash
agora-code summarize agora_code/session.py
agora-code summarize large_file.py --threshold 50
```

---

#### `agora-code track-diff`

Capture a git diff and store a compact summary. Called automatically by hooks.

```bash
agora-code track-diff agora_code/auth.py
agora-code track-diff --all               # all uncommitted files
agora-code track-diff auth.py --committed # diff against HEAD~1
```

---

#### `agora-code file-history`

Show the tracked change history for a file.

```bash
agora-code file-history agora_code/auth.py
agora-code file-history agora_code/session.py --limit 5
```

---

#### `agora-code index`

Re-index a file into the DB. Called automatically by `on-edit.sh`.

```bash
agora-code index agora_code/auth.py
```

---

### Listing commands

```bash
agora-code list-sessions        # archived session records
agora-code list-learnings       # permanent findings
agora-code list-snapshots       # AST summaries per file
agora-code list-symbols         # indexed functions and classes
agora-code list-file-changes    # per-file diff history
agora-code list-api-calls       # HTTP calls from serve/chat
```

All accept `-n` / `--limit`. `list-symbols` also accepts `--file <path>`.

---

### API tools

These commands are for working with HTTP APIs — scanning routes, running an MCP server for your API, and calling it in natural language.

#### `agora-code scan`

Discover all API routes in a codebase or from a live URL.

```bash
agora-code scan ./my-fastapi-app
agora-code scan https://api.example.com
agora-code scan ./my-app --output routes.json
```

---

#### `agora-code serve`

Start an MCP server for your API. Plug into Claude Desktop or Cursor.

```bash
agora-code serve ./my-api --url http://localhost:8000
```

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

---

#### `agora-code chat`

Interactive natural-language chat against your API.

```bash
agora-code chat ./my-api --url http://localhost:8000
```

---

#### `agora-code memory-server`

Start a project-agnostic MCP server for coding memory. Exposes session and learning tools to any MCP-compatible editor.

```bash
agora-code memory-server
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
| `search_symbols` | Search across all indexed symbols |
| `recall_file_history` | See what changed in a file across past sessions |
| `complete_session` | Archive session to long-term memory when done |
| `list_sessions` | Find past sessions |
| `get_memory_stats` | DB usage stats |

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

Override DB path: `export AGORA_CODE_DB=/path/to/custom/memory.db`

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI embeddings + LLM scan |
| `GEMINI_API_KEY` | Gemini embeddings + LLM scan |
| `ANTHROPIC_API_KEY` | Claude for LLM scan + workflow detection |
| `EMBEDDING_PROVIDER` | `auto` / `openai` / `gemini` / `local` (default: `auto`) |
| `LOCAL_EMBEDDING_MODEL` | sentence-transformers model (default: `BAAI/bge-small-en-v1.5`) |
| `AGORA_CODE_DB` | Override memory DB path |
| `AGORA_AUTH_TOKEN` | Default bearer token for API calls |

---

## Project Scoping

All sessions and learnings are scoped per project via git remote URL:

```bash
git remote get-url origin   # → used as project_id
```

Falls back to directory name if no git remote is set.

---

## Troubleshooting

### Testing the pre-read hook manually

```bash
echo '{"file_path": "/path/to/your/large-file.py"}' | bash .claude/hooks/pre-read.sh
echo "exit code: $?"
```

- Summary printed + `exit code: 2` = hook working correctly (blocked and served summary)
- No output + `exit code: 0` = file is under the threshold (pass through)

Check error log if something goes wrong:
```bash
cat /tmp/agora-pre-read-error.log
```

---

### Common issues

**`agora-code` not found in hooks**

Hooks run in a non-interactive shell. Most common causes:

*macOS — permission error during install:*
```bash
pip install --user git+https://github.com/thebnbrkr/agora-code.git
export PATH="$(python3 -m site --user-base)/bin:$PATH"
# add the export to ~/.zshrc or ~/.bashrc
```

*virtualenv or pyenv:* Get the full path and use it in the hook scripts:
```bash
which agora-code
# open .claude/hooks/pre-read.sh etc. and replace `agora-code` with the full path
```

**No embeddings / semantic search not working**

Expected if you haven't set an API key. Everything still works with keyword search. To enable semantic search:
```bash
export OPENAI_API_KEY=sk-...
# or: export GEMINI_API_KEY=...
# or: pip install "agora-code[local]"  (offline)
```

---

## Roadmap

- **Cursor hook support**: hook-based auto-tracking for Cursor. Currently MCP only.
- **Gemini CLI hooks**: `BeforeAgent` / `AfterAgent` hooks in progress.
- **GitHub Copilot**: hook support not yet available.
- **Subagent awareness**: injecting session context into Claude subagents.
- **Error memory**: `PostToolUseFailure` hook to track recurring errors.

See [FUTURE_HOOKS.md](FUTURE_HOOKS.md) for the full roadmap.

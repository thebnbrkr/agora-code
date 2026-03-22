# Using agora-code with Kiro

agora-code gives Kiro persistent memory across sessions — goals, learnings, file history, and search logs survive restarts and context resets.

## Setup

### 1. Install agora-code

```bash
pip install agora-code
```

Or for local development (editable install):

```bash
pip install -e /path/to/agora-code
```

### 2. Add the MCP server to your project

Create `.kiro/settings/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "agora-memory": {
      "command": "agora-code",
      "args": ["memory-server"],
      "autoApprove": [
        "get_session_context", "save_checkpoint", "store_learning",
        "recall_learnings", "complete_session", "get_memory_stats",
        "list_sessions", "recall_file_history", "get_file_symbols",
        "search_symbols", "summarize_file", "read_file_range",
        "index_file", "log_search", "store_team_learning", "recall_team"
      ]
    }
  }
}
```

> If `agora-code` isn't on your PATH, run `which agora-code` to find it and use that full path in the config.

### 3. Add the steering document

Copy `.kiro/steering/agora-memory.md` from this repo into your project. This tells Kiro when and how to use each tool automatically — no manual prompting needed.

### 4. Add the hooks

Copy the `.kiro/hooks/` folder from this repo into your project. These 11 hooks automate everything:

| Hook | Trigger | Action |
|---|---|---|
| `agora-session-inject` | Every prompt | Injects last session context (shell, 0 credits) |
| `agora-auto-checkpoint` | Agent stop | Saves progress checkpoint (shell, 0 credits) |
| `agora-inject-before-task` | Spec task start | Loads context before each task (shell, 0 credits) |
| `checkpoint-after-task` | Spec task end | Saves checkpoint after task (shell, 0 credits) |
| `agora-summarize-before-read` | Before `readCode` | Gets AST outline, reads only relevant section |
| `agora-summarize-before-readfile` | Before `readFile` | Same for regular file reads |
| `agora-summarize-before-readmulti` | Before `readMultipleFiles` | Same for multi-file reads |
| `agora-index-after-write` | After `writeFile` | Indexes updated symbols into memory DB |
| `agora-index-after-strreplace` | After `strReplace` | Indexes after targeted edits |
| `agora-index-after-editcode` | After `editCode` | Indexes after AST-based edits |
| `agora-log-grep-results` | After `grepSearch` | Logs search query + indexes matched files |

Restart Kiro after adding hooks for them to take effect.

## What you get

Once set up, Kiro automatically:

- **Remembers what you were working on** — session context is injected before every prompt
- **Saves progress** — checkpoints after every agent turn and spec task
- **Stores discoveries** — non-obvious findings are saved and recalled in future sessions
- **Reads large files efficiently** — AST summaries before reads, then targeted line ranges (90%+ token reduction)
- **Tracks your searches** — grep queries and matched files are logged persistently
- **Indexes edited files** — symbols become searchable across sessions after every edit

## Available MCP tools

| Tool | What it does |
|---|---|
| `get_session_context` | Load last session — goal, hypothesis, next steps, files changed |
| `save_checkpoint` | Save current progress mid-session |
| `complete_session` | Archive session to long-term memory when done |
| `store_learning` | Save a non-obvious finding for future sessions |
| `recall_learnings` | Search past findings before starting a task |
| `store_team_learning` | Save a finding shared across the whole team/project |
| `recall_team` | Search team-wide knowledge |
| `summarize_file` | Get AST outline of a file with function names and line numbers |
| `read_file_range` | Read a specific line range from a file |
| `index_file` | Index a file's symbols into the memory DB |
| `get_file_symbols` | Get all indexed symbols for a file |
| `search_symbols` | Search symbols across all indexed files |
| `recall_file_history` | See all past changes to a file across sessions |
| `log_search` | Log a search query and its matched files |
| `get_memory_stats` | Check DB stats — session count, learning count, symbol count |
| `list_sessions` | List all past sessions |

## Verifying it works

In Kiro's terminal:

```bash
agora-code status          # DB path and row counts
agora-code list-learnings  # everything stored so far
agora-code list-sessions   # all past sessions
agora-code inject          # manually trigger session inject, see output
agora-code recall "your query"  # test semantic search
```

## How it works

All memory is stored in a local SQLite database at `~/.agora-code/memory.db`. Nothing leaves your machine. The MCP server (`agora-code memory-server`) exposes this database to Kiro via the Model Context Protocol.

The shell hooks (`agora-session-inject`, `agora-auto-checkpoint`, etc.) run `agora-code inject` and `agora-code checkpoint` as zero-credit shell commands. The `askAgent` hooks (summarize, index, log) use MCP tool calls and consume a small amount of credits.

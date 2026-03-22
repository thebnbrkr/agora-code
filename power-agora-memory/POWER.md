---
name: "agora-memory"
displayName: "Agora Memory — Persistent Agent Memory"
description: "Persistent memory layer for AI coding agents. Session context, learnings, and file history survive context resets, new conversations, and IDE restarts."
keywords: ["memory", "session", "checkpoint", "recall", "learning", "persist", "context", "agora", "sub-agents", "context-limit"]
author: "Agora"
---

# Agora Memory Power

## Overview

AI coding agents forget everything between sessions. You spend time figuring out that a certain endpoint rejects `+` in emails, or that a particular middleware is causing a bug — and next session, you explain it all over again. Agora Memory fixes this.

**Key capabilities:**
- **Session continuity** — loads your last goal, hypothesis, and discoveries at the start of every conversation
- **Persistent learnings** — store non-obvious findings that are recalled in future sessions automatically
- **Token-efficient file reading** — AST outlines of large files before reading, saving 90%+ tokens
- **Sub-agent awareness** — subagents get session context injected so they don't start blind
- **Spec task continuity** — checkpoints after every task so multi-session specs resume exactly where you left off

**Perfect for:**
- Working on large codebases across multiple sessions
- Teams sharing discoveries and gotchas across projects
- Reducing repeated context-setting at the start of every conversation
- Making sub-agents and parallel agent runs context-aware
- Running long multi-task specs without losing state

## Onboarding

### Step 1: Install agora-code

```bash
pip install git+https://github.com/thebnbrkr/agora-code.git
```

Get the full binary path (needed for hooks):
```bash
which agora-code
```

### Step 2: Install hooks

Create the following files in `.kiro/hooks/`. Replace `agora-code` with the full path from Step 1 if it's not on your PATH.

**`.kiro/hooks/agora-session-inject.kiro.hook`**
```json
{
  "enabled": true,
  "name": "agora: session inject",
  "description": "Inject last session context before every prompt",
  "version": "1",
  "when": { "type": "userPromptSubmit" },
  "then": {
    "type": "runCommand",
    "command": "agora-code inject --quiet 2>/dev/null || true",
    "timeout": 10
  }
}
```

**`.kiro/hooks/agora-checkpoint.kiro.hook`**
```json
{
  "enabled": true,
  "name": "agora: auto checkpoint",
  "description": "Save session checkpoint after every agent turn",
  "version": "1",
  "when": { "type": "agentStop" },
  "then": {
    "type": "runCommand",
    "command": "agora-code checkpoint --quiet 2>/dev/null || true",
    "timeout": 10
  }
}
```

**`.kiro/hooks/agora-pre-read.kiro.hook`**
```json
{
  "enabled": true,
  "name": "agora: summarize before read",
  "description": "Get AST outline before reading a file to save tokens",
  "version": "1",
  "when": { "type": "preToolUse", "toolName": "readCode" },
  "then": {
    "type": "askAgent",
    "prompt": "Before reading this file, call summarize_file from agora-memory to get the AST outline with function names and line numbers. Then use read_file_range to read only the section relevant to the current task."
  }
}
```

**`.kiro/hooks/agora-post-write.kiro.hook`**
```json
{
  "enabled": true,
  "name": "agora: index after write",
  "description": "Index file symbols into memory DB after editing",
  "version": "1",
  "when": { "type": "postToolUse", "toolName": "fsWrite" },
  "then": {
    "type": "askAgent",
    "prompt": "Call index_file from agora-memory for the file that was just written so its symbols are searchable in future sessions."
  }
}
```

**`.kiro/hooks/agora-task-inject.kiro.hook`**
```json
{
  "enabled": true,
  "name": "agora: inject before task",
  "description": "Load session context before each spec task starts",
  "version": "1",
  "when": { "type": "preTaskExecution" },
  "then": {
    "type": "runCommand",
    "command": "agora-code inject --quiet 2>/dev/null || true",
    "timeout": 10
  }
}
```

**`.kiro/hooks/agora-task-checkpoint.kiro.hook`**
```json
{
  "enabled": true,
  "name": "agora: checkpoint after task",
  "description": "Save progress after each spec task completes",
  "version": "1",
  "when": { "type": "postTaskExecution" },
  "then": {
    "type": "runCommand",
    "command": "agora-code checkpoint --quiet 2>/dev/null || true",
    "timeout": 10
  }
}
```

---

## Available MCP Tools

### Session Memory

**`get_session_context`** — Load last session state at conversation start
- `level` (optional): `index` / `summary` / `detail` / `full` — how much context to return (default: `detail`)

**`save_checkpoint`** — Save current session state
- `goal` (optional): What you're trying to accomplish
- `hypothesis` (optional): Current working theory
- `action` (optional): What's being done right now
- `next_steps` (optional): Array of next steps
- `blockers` (optional): Array of current blockers
- `files_changed` (optional): Array of `"file.py:what changed"` strings

**`complete_session`** — Archive session to long-term memory when done
- `summary` (required): What was accomplished
- `outcome` (optional): `success` / `partial` / `abandoned`

**`list_sessions`** — Browse past sessions
- `limit` (optional): Number of sessions to return (default: 10)

### Learnings

**`store_learning`** — Store a permanent finding in long-term memory
- `finding` (required): What was learned
- `evidence` (optional): Supporting example or context
- `confidence` (optional): `confirmed` / `likely` / `hypothesis`
- `tags` (optional): Array of tags for categorization

**`recall_learnings`** — Search past learnings by semantic similarity
- `query` (required): What to search for
- `limit` (optional): Max results (default: 5)

### File Intelligence

**`summarize_file`** — Get token-efficient AST outline of any file
- `file_path` (required): Path to file (absolute or repo-relative)
- `max_tokens` (optional): Token budget for summary (default: 2000)

**`read_file_range`** — Read specific line range from a file
- `file_path` (required): Path to file
- `start_line` (required): First line to read (1-indexed)
- `end_line` (optional): Last line (inclusive); omit to read to end

**`index_file`** — Index a file's symbols into memory DB
- `file_path` (required): Path to file to index

**`get_file_symbols`** — Get all indexed functions/classes for a file
- `file_path` (required): Path to file
- `limit` (optional): Max symbols to return (default: 50)

**`search_symbols`** — Search symbols across entire codebase
- `query` (required): Function name, concept, or description
- `symbol_type` (optional): `function` / `method` / `class`
- `limit` (optional): Max results (default: 10)

**`recall_file_history`** — See what changed in a file across past sessions
- `file_path` (required): Path to file
- `limit` (optional): Max history entries (default: 10)

### Stats

**`get_memory_stats`** — DB usage stats — session count, learning count, symbols indexed
- No parameters required

---

## When to Use Each Tool

| Situation | Tool |
|---|---|
| Starting a new conversation | `get_session_context` |
| Completed a meaningful step | `save_checkpoint` |
| Discovered something non-obvious | `store_learning` |
| Starting a task — check if already solved | `recall_learnings` |
| About to read a large file | `summarize_file` → then `read_file_range` |
| Just edited a file | `index_file` |
| Session fully done | `complete_session` |

## Agent Rules

1. **Always call `get_session_context` at the start** of every new conversation before doing anything else.
2. **Before reading any large file**, call `summarize_file` first to get the AST outline, then `read_file_range` for just the section needed. Saves 90%+ tokens.
3. **Call `store_learning`** when you discover something non-obvious — gotchas, patterns, API quirks, constraints.
4. **Call `save_checkpoint`** after any meaningful step completes.
5. **Call `complete_session`** when the user says they're done.

## Tips

1. **Sub-agents start blind** — if using parallel agents or specs with many tasks, the hooks ensure every sub-agent gets session context injected automatically
2. **Learnings are global** — stored findings are recalled across all your projects, not just the current one
3. **No API key required** — works with keyword search out of the box; set `OPENAI_API_KEY` or `GEMINI_API_KEY` for semantic recall
4. **Editable install** — run `pip install -e /path/to/agora-code` to use local changes immediately without reinstalling
5. **Check what's stored** — run `agora-code memory` in terminal to see all sessions, learnings, and indexed symbols

---

**Package:** `agora-code` (pip)
**Source:** [github.com/thebnbrkr/agora-code](https://github.com/thebnbrkr/agora-code)
**License:** Apache-2.0
**Install:** `pip install git+https://github.com/thebnbrkr/agora-code.git`

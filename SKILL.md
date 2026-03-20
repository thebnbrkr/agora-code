# agora-code

Persistent memory and context reduction for AI coding agents.

Two problems it solves:
1. **Context window limits** — large files are summarized before reading, tokens spent on signal not noise
2. **Memory loss between sessions** — what you did, why, and what changed is persisted and injected at session start

## How it works

Every file read is summarized via Tree-sitter/AST before loading. Every file edit generates a change note. Every git commit automatically derives learnings from those change notes and ties them to the commit SHA. On session start, the most relevant learnings (from recent commits on your branch) are injected alongside uncommitted work context and git state.

**Everything runs automatically via hooks after `agora-code install-hooks --claude-code`.**

---

## Claude Code setup

```bash
pip install agora-code
agora-code install-hooks --claude-code
agora-code inject   # run once at session start
```

## Cursor / Claude Desktop setup

```bash
agora-code memory-server
```

```json
{"mcpServers": {"agora-code": {"command": "agora-code", "args": ["memory-server"]}}}
```

---

## Session lifecycle

```
Session start  →  agora-code inject
                  Loads: last checkpoint, commit learnings, uncommitted
                  work notes, git state, symbol index for dirty files

Working        →  after every file edit, run:
                  agora-code track-diff <file> --note "what changed and why"
                  (you already know — write it inline, hooks can't)

                  hooks run automatically for everything else:
                  on file read:   summarize → index symbols → file_snapshots
                  on git commit:  tag file_changes → store learning per file
                                  → linked in commit_learnings junction

Session end    →  agora-code complete --summary "..."
```

---

## What inject loads

```
LAST CHECKPOINT      last substantial session goal + decisions

UNCOMMITTED WORK     per-file change notes for dirty files
                     "changed _check_expiry() to use utcnow() — fixes tz offset"

LEARNINGS            from last 3 commits on current branch + last commit on main
                     "[abc1234] changed authenticate() signature to accept tz param"

GIT LOG              last 6 commits

SYMBOL INDEX         function:line for dirty files
```

---

## CLI reference

### Inspect what's in memory

| Command | What it shows |
|---|---|
| `agora-code show` | Everything inject loaded — tabular |
| `agora-code notes` | AI-written change notes for all files |
| `agora-code notes <file>` | Change notes for a specific file |
| `agora-code commit-log` | Learnings stored per commit |
| `agora-code commit-log <sha>` | Learnings for a specific commit |
| `agora-code file-history <file>` | Full change history for a file |
| `agora-code list-symbols` | All indexed functions/classes |
| `agora-code list-learnings` | All learnings in DB |
| `agora-code memory` | DB path, counts, recent sessions |

### Memory operations

| Command | What it does |
|---|---|
| `agora-code inject` | Print session context for injection |
| `agora-code learn-from-commit [sha]` | Store learnings from a commit (auto on commit) |
| `agora-code learn "<finding>"` | Manually store a learning |
| `agora-code recall "<query>"` | Search learnings by keyword |
| `agora-code track-diff <file> --note "..."` | **After every edit** — what changed, what it calls/depends on, outcome. Tag `#not_kept` if reverted; `#kept` is auto-added on commit |
| `agora-code summarize <file>` | Summarize a file before reading |
| `agora-code checkpoint --goal "..."` | Save mid-session progress |
| `agora-code complete --summary "..."` | Archive session |

---

## MCP tool reference (Cursor / Claude Desktop)

| Tool | When to use |
|---|---|
| `get_session_context` | Session start — loads checkpoint, learnings, git state |
| `save_checkpoint` | After completing a meaningful step |
| `store_learning` | Non-obvious finding: bug, gotcha, architectural decision |
| `recall_learnings` | Before starting something — check if solved before |
| `get_file_symbols` | Indexed functions/classes for a file with line numbers |
| `search_symbols` | Search across all indexed symbols |
| `recall_file_history` | What changed in a file across past sessions |
| `complete_session` | Archive session to long-term memory |
| `list_sessions` | Find past sessions |
| `get_memory_stats` | DB usage stats |

---

## Storage

Single SQLite DB at `~/.agora-code/memory.db` — shared across projects, isolated by `project_id` (git remote URL).

| Table | What's in it |
|---|---|
| `sessions` | Session state, goal, branch, commit SHA |
| `learnings` | Project intelligence, tagged by commit SHA |
| `commit_learnings` | Junction: commit SHA → learning IDs |
| `file_snapshots` | AST summaries per file |
| `symbol_notes` | Per-function: name, line numbers, signature, code block |
| `file_changes` | Per-edit change notes with diff snippets |

LLM for change notes: in Claude Code, `ANTHROPIC_API_KEY` is available automatically so Claude is used without any setup. Outside Claude Code, set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` — or `LLM_PROVIDER=claude|openai|gemini` to force a provider. Falls back to regex if none available.

# agora-code — Claude Code Instructions

This project provides persistent memory and API discovery for AI agents.

## How it works

Everything is automatic via Claude Code hooks. When you read a file, symbols and code blocks are indexed. When a session ends, the transcript is parsed into a structured checkpoint. On every prompt, relevant learnings are recalled.

**You do not need to manually run `agora-code learn` or `agora-code recall`** — the hooks handle it.

## Always do this

- **At session start**, run `agora-code inject` to load previous session context
- **STOP — before ANY Read tool call on a file over ~100 lines:**
  1. Run `agora-code summarize <file>` first
  2. Then use `offset+limit` to read ONLY the sections you need
  Skipping this wastes the entire point of the tool.
- **After every file edit**, run:
  ```
  agora-code track-diff <file> --note "one sentence: what changed and why"
  ```
  Write what function/class changed, what it calls/depends on and where, and the outcome. Tag `#not_kept` if you reverted — kept changes are auto-tagged `#kept` on commit.
  Example: `"tried caching in validate_user() via redisupload() in redis_db.py to reduce DB hits — didn't work #not_kept"`

## Tool reference

| Command | When to use |
|---|---|
| `agora-code inject` | Session start — loads structured checkpoint, learnings, git state, symbol index |
| `agora-code summarize <file>` | Before reading large files — 75%+ token reduction |
| `agora-code learn "<text>"` | Force-save a specific finding right now (optional — auto-done by on-stop.sh) |
| `agora-code recall "<query>"` | Search past findings (optional — auto-done by on-prompt.sh) |
| `agora-code checkpoint --goal "..."` | Save progress mid-task |
| `agora-code status` | Check current session and DB stats |
| `agora-code complete --summary "..."` | Archive session to long-term memory when done |

## Session lifecycle

```
Start     → agora-code inject
Working   → agora-code summarize <file> before reading any file over ~100 lines
Step done → agora-code checkpoint --goal "..." --action "..."
All done  → agora-code complete --summary "..."
```

## What the hooks store automatically

- **symbol_notes**: every function/class with `start_line`, `end_line`, `signature`, `note`, `code_block`
- **file_snapshots**: compressed AST outline of each file read
- **learnings**: structured checkpoint after each session (goal, decisions, next_steps, blockers)
- **file_changes**: diff history per file, tagged with commit SHA on commit

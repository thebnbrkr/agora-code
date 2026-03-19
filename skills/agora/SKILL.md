---
name: agora-code
description: Use agora-code memory tools — inject session context, learn findings, recall past work, checkpoint progress, and summarize large files
---

## First-time setup (required)

agora-code needs its CLI installed and hooks wired up. Run this once per project:

```bash
pip install git+https://github.com/thebnbrkr/agora-code.git
agora-code install-hooks --claude-code
```

Then restart Claude Code. After that, everything below is automatic.

---

agora-code gives you persistent memory across sessions. Hooks handle most things automatically, but **you must follow the rules below** — they are not optional.

## Your rules (always follow these)

1. **Before reading any file over ~100 lines** — run `agora-code summarize <file>` first. Do not use the Read tool or an Explore subagent on a large file without summarizing first. This is mandatory, not optional.
2. **At session start** — run `agora-code inject` to load prior context (checkpoints, learnings, git state, symbol index).
3. **When done with a task** — run `agora-code complete --summary "..."` to archive the session.

## What the hooks handle automatically (no action needed)

| Hook | Event | Does |
|---|---|---|
| `pre-read.sh` | PreToolUse(Read) | Intercepts large files — auto-summarizes before Claude reads |
| `on-read.sh` | PostToolUse(Read) | Indexes symbols + code blocks into DB |
| `on-grep.sh` | PostToolUse(Grep) | Indexes files matched by grep into DB |
| `on-edit.sh` | PostToolUse(Write/Edit) | Re-indexes symbols, tracks diff |
| `on-bash.sh` | PostToolUse(Bash) | Tags committed files with SHA on `git commit` |
| `on-prompt.sh` | UserPromptSubmit | Auto-sets goal, recalls relevant learnings |
| `on-stop.sh` | Stop | Parses transcript → structured checkpoint |

PostCompact re-injects context automatically after context compaction.

## Session lifecycle

```
SessionStart  → agora-code inject                   # load prior context
Working       → agora-code summarize <file>          # before every large file
Step done     → agora-code checkpoint --goal "..."   # optional mid-task save
All done      → agora-code complete --summary "..."  # archive session
```

## Manual commands reference

| Command | When to use |
|---|---|
| `agora-code inject` | Load prior session context |
| `agora-code summarize <file>` | **Before reading any file over ~100 lines** |
| `agora-code learn "<text>"` | Force-save a specific finding right now |
| `agora-code recall "<query>"` | Search past findings for a topic |
| `agora-code checkpoint --goal "..."` | Save progress mid-task |
| `agora-code status` | Check session and DB stats |
| `agora-code complete --summary "..."` | Archive session when done |

## inject output format

`agora-code inject` outputs ~300 tokens of structured context:

```
LAST CHECKPOINT
  goal / decisions / next_steps / blockers / files / branch + commit

LEARNINGS
  recent findings tagged by type (decision → / blocker ! / next » / finding ·)

GIT LOG
  last 6 commits

UNCOMMITTED
  dirty files

SYMBOL INDEX
  function:line for dirty files (avoids re-reading them)
```

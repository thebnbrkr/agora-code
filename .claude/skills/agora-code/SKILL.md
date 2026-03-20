---
name: agora-code
description: Use agora-code memory tools — inject session context, learn findings, recall past work, checkpoint progress, and summarize large files
---

agora-code provides persistent memory across Claude Code sessions. Most things happen **automatically via hooks** — you rarely need to call commands manually.

## What happens automatically

| Hook | Event | Does |
|---|---|---|
| `pre-read.sh` | PreToolUse(Read) | Intercepts large files — auto-summarizes before Claude reads |
| `on-read.sh` | PostToolUse(Read) | Indexes symbols + code blocks into DB |
| `on-grep.sh` | PostToolUse(Grep) | Indexes files matched by grep into DB |
| `on-edit.sh` | PostToolUse(Write/Edit) | Re-indexes symbols, tracks diff |
| `on-bash.sh` | PostToolUse(Bash) | Tags committed files with SHA on `git commit` |
| `on-prompt.sh` | UserPromptSubmit | Recalls relevant learnings for this prompt |
| `on-stop.sh` | Stop | Parses transcript → structured checkpoint (goal, decisions, next steps, blockers) |

PostCompact re-injects context automatically after Claude compacts the conversation.

## Session lifecycle

```
SessionStart  → agora-code inject (auto)            # loads structured context
Working       → hooks fire silently                  # zero manual commands needed
Step done     → agora-code checkpoint --goal "..."   # optional mid-task save
All done      → agora-code complete --summary "..."  # archive to long-term memory
```

## Manual commands (when needed)

| Command | When to use |
|---|---|
| `agora-code inject` | Manually reload context (auto-fires on SessionStart) |
| `agora-code summarize <file>` | Before reading any file over ~100 lines |
| `agora-code learn "<text>"` | Force-save a specific finding right now |
| `agora-code recall "<query>"` | Search past findings for a topic |
| `agora-code checkpoint --goal "..."` | Save progress mid-task |
| `agora-code status -p` | Check session and DB stats **for this project** |
| `agora-code complete --summary "..."` | Archive session when done |

## inject output format

`agora-code inject` outputs ~300 tokens of structured context — a combination of:

```
LAST CHECKPOINT
  goal / decisions / next_steps / blockers / files / branch + commit
  (extracted from on-stop.sh parsing the previous session's transcript)

LEARNINGS
  recent findings for this project tagged by type:
    →  decision     !  blocker     »  next step     ·  finding

GIT LOG
  last 6 commits (live from git)

UNCOMMITTED
  dirty files (live from git status)

SYMBOL INDEX
  function:line for dirty/recently-read files (avoids re-reading them)
```

## Goal behavior

**Do not ask the user to set a goal.** Infer it automatically from what they're working on and update it as the conversation evolves. The goal is used internally by on-stop.sh to structure the checkpoint — it should reflect what actually happened, not a manually typed label.

## Rules

- **Always** run `agora-code status -p` (not `status`) to see per-project stats
- **Always** run `agora-code summarize <file>` before reading any file over ~100 lines
- `agora-code learn` is optional — on-stop.sh auto-extracts findings from transcripts
- `agora-code recall` is optional — on-prompt.sh auto-recalls on every prompt

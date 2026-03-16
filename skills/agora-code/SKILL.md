---
name: agora-code
description: Use agora-code memory tools — inject session context, learn findings, recall past work, checkpoint progress, and summarize large files
---

agora-code provides persistent memory across Claude Code sessions. Most things happen **automatically via hooks** — you rarely need to call commands manually.

## What happens automatically

| Hook | Fires on | Does |
|---|---|---|
| `on-read.sh` | Every file read | Indexes symbols + code blocks into DB |
| `on-edit.sh` | Every file write/edit | Re-indexes symbols, tracks diff |
| `on-bash.sh` | Every bash command | Tags committed files with SHA on `git commit` |
| `on-stop.sh` | Session end | Parses transcript → structured checkpoint (goal, decisions, next steps, blockers) |
| `on-prompt.sh` | Every user prompt | Auto-sets goal, recalls relevant learnings |

## Session lifecycle

```
SessionStart  → agora-code inject (auto)           # loads structured context
Working       → hooks fire silently                 # zero manual commands needed
Step done     → agora-code checkpoint --goal "..."  # optional manual save
All done      → agora-code complete --summary "..." # archive to long-term memory
```

## Manual commands (when needed)

| Command | When to use |
|---|---|
| `agora-code inject` | Manually reload context (auto-fires on SessionStart) |
| `agora-code summarize <file>` | Before reading any file over ~200 lines |
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

## Rules

- **Always** run `agora-code summarize <file>` before reading any file over ~200 lines
- `agora-code learn` is optional — on-stop.sh auto-extracts findings from transcripts
- `agora-code recall` is optional — on-prompt.sh auto-recalls on every prompt

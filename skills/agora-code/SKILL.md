---
name: agora-code
description: Use agora-code memory tools — inject session context, learn findings, recall past work, checkpoint progress, and summarize large files
---

agora-code provides persistent memory across Claude Code sessions. Use these commands throughout your work:

## Session lifecycle

```
Session start  → agora-code inject              # load goal, hypothesis, last steps
Before large file → agora-code summarize <file> # 90%+ token reduction
Discovery      → agora-code learn "<finding>"   # save non-obvious findings
Step done      → agora-code checkpoint --goal "..." --action "..."
All done       → agora-code complete --summary "..."
```

## Tool reference

| Command | When to use |
|---|---|
| `agora-code inject` | Session start — loads goal, hypothesis, last steps |
| `agora-code summarize <file>` | Before reading any file over ~200 lines |
| `agora-code learn "<text>"` | You find a bug, gotcha, architectural decision, or non-obvious pattern |
| `agora-code recall "<query>"` | Before starting work — check if it's been solved before |
| `agora-code checkpoint --goal "..."` | After completing a meaningful step |
| `agora-code scan .` | Discover API routes in the codebase |
| `agora-code status` | Check current session and memory stats |
| `agora-code complete --summary "..."` | Archive session to long-term memory when done |

## Rules

- **Always** run `agora-code inject` at the start of every session
- **Always** run `agora-code summarize <file>` before reading any file over ~200 lines
- **Always** run `agora-code recall "<query>"` before starting any task that might have been worked on before
- Run `agora-code learn` whenever you find something non-obvious (bugs, gotchas, decisions)

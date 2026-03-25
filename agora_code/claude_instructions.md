# agora-code — Claude Code Instructions

This project provides persistent memory and API discovery for AI agents.

## Always do this

- **Before reading any file over ~50 lines**, run `agora-code summarize <file>` first
- **At session start**, run `agora-code inject` to load previous session context
- **When you discover something non-obvious**, run `agora-code learn "<finding>"`
- **Use `agora-code recall "<query>"` before starting any task** that might have been worked on before

## Tool reference

| Command | When to use |
|---|---|
| `agora-code inject` | Session start — loads goal, hypothesis, last steps |
| `agora-code summarize <file>` | Before reading large files — 75%+ token reduction |
| `agora-code learn "<text>"` | You find a bug, gotcha, or decision worth remembering |
| `agora-code recall "<query>"` | Before starting work — check if it's been solved before |
| `agora-code checkpoint --goal "..."` | After completing a meaningful step |
| `agora-code scan .` | To discover API routes in the codebase |
| `agora-code status` | Check current session and memory stats |

## Session lifecycle

```
Start  → agora-code inject
Working → agora-code summarize <file> before reading large files
Finding → agora-code learn "<what you found>"
Step done → agora-code checkpoint --goal "..." --action "..."
All done → agora-code complete --summary "..."
```

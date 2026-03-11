# agora-memory — Agent Skills Reference

## When to use each tool

| Tool | USE WHEN |
|---|---|
| `get_session_context` | Starting a new chat or task — always call this first |
| `save_checkpoint` | After completing a meaningful step, before switching tasks, or when context window is filling |
| `store_learning` | You discover something non-obvious: a bug, gotcha, pattern, or decision the team should remember |
| `recall_learnings` | Before starting something that might have been solved before, or when debugging unexpected behaviour |
| `recall_file_history` | Starting work on a file — see what changed across past sessions without reading the whole file |
| `complete_session` | Task is fully done and you want to archive it to long-term memory |
| `list_sessions` | User asks what was worked on before, or you need to find a past session |
| `store_team_learning` | Finding applies to everyone on the team (shared conventions, API quirks, deployment gotchas) |
| `recall_team` | Looking for shared team knowledge, or user asks "has anyone figured out X?" |
| `get_memory_stats` | Debugging or curious about storage usage |

## Session lifecycle

```
Session start  → get_session_context()          # what was I doing?
Working        → save_checkpoint(...)            # periodic saves
Discovery      → store_learning(...)             # non-obvious findings
Done           → complete_session(summary=...)   # archive
```

## Tips

- `save_checkpoint` needs at minimum one of: `goal`, `hypothesis`, `action`, `files_changed`
- `store_learning` confidence levels: `confirmed` (tested), `likely` (strong evidence), `hypothesis` (untested)  
- File history is populated automatically via PostToolUse hooks — no manual action needed
- Team learnings persist across all projects and agents sharing the same DB path

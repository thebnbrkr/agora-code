---
inclusion: always
---

# Persistent Memory via agora-memory MCP

You have access to the `agora-memory` MCP server, which gives you persistent memory across sessions — goals, discoveries, file history, and learnings survive restarts.

## When to use each tool

| Situation | Tool |
|---|---|
| Starting a new conversation | `get_session_context` |
| Completed a meaningful step | `save_checkpoint` |
| Discovered something non-obvious | `store_learning` |
| Starting a task — check if already solved | `recall_learnings` |
| Session is fully done | `complete_session` |
| About to read a large file | `summarize_file` first, then read only the section you need |
| Need a specific line range from a file | `read_file_range` |
| Just read/edited a file, want symbols searchable | `index_file` |

## Rules

1. **Always call `get_session_context` at the start** of every new conversation before doing anything else. This loads what was being worked on last session.

1a. **Before reading any file**, call `summarize_file` first to get an AST outline with function names and line numbers. Then use `read_file_range` to read only the section you need. This saves 90%+ tokens on large files.

2. **Call `save_checkpoint`** after any meaningful step completes (task done, bug fixed, decision made). Include `goal`, `action`, and `files_changed`.

3. **Call `store_learning`** whenever you discover something non-obvious — a gotcha, a pattern, an API quirk, a constraint. These are searchable across all future sessions.

4. **Call `recall_learnings`** before starting a new task to check if it's been attempted or solved before.

5. **Call `complete_session`** when the user says they're done or wrapping up.

## Example flow

```
Session start:
  → get_session_context()            # What was I doing?

Before new task:
  → recall_learnings("auth token")   # Solved before?

After fixing a bug:
  → store_learning("JWT tokens expire in 15min, refresh endpoint is /auth/refresh")
  → save_checkpoint(goal="...", action="fixed auth bug", files_changed=["auth.py"])

Session end:
  → complete_session(summary="Fixed auth + added retry logic")
```

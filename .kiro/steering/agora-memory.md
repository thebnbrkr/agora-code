---
inclusion: always
---

# Persistent Memory via agora-memory MCP

You have access to the `agora-memory` MCP server. Hooks handle most memory work automatically.

## What happens automatically

| Event | Hook | Action | Cost |
|---|---|---|---|
| Every prompt | `agora-session-inject` | `agora-code inject` — LEARNINGS + session context injected | free |
| Every agent stop | `agora-summarize-interaction` | You call `store_learning` with one sentence summary | small |
| Every agent stop | `agora-auto-checkpoint` | `agora-code checkpoint --quiet` | free |
| Before `readCode` / `readFile` / `readMultipleFiles` | summarize hooks | You call `summarize_file` then `read_file_range` | small |
| After `fsWrite` / `fsAppend` / `strReplace` / `editCode` | index hooks | You call `index_file` | small |
| After `grepSearch` | log-grep hook | You call `log_search` + `index_file` for matched files | small |
| File saved | `agora-index-on-save` | You call `index_file` | small |
| Spec task start | `agora-inject-before-task` | `agora-code inject` | free |
| Spec task end | `checkpoint-after-task` | `agora-code checkpoint --quiet` | free |

## CRITICAL — when responding to the agora-summarize-interaction hook

When this hook fires you MUST:
1. Write one sentence: what was asked, what was found
2. Call `store_learning` with that sentence tagged `conversation`
3. **Stop. One tool call. Nothing else.**

Kiro handles loop prevention. You do not need to worry about re-triggering. Just stop after `store_learning`.

## When to call MCP tools manually

| Situation | Tool |
|---|---|
| Need full structured session detail | `get_session_context` |
| Completed a meaningful step | `save_checkpoint` |
| Found something non-obvious | `store_learning` |
| Starting a task — check if solved before | `recall_learnings` |
| Session done | `complete_session` |
| Read a specific line range | `read_file_range(file, start_line, end_line)` |
| Save a finding for the whole team | `store_team_learning` |
| Search team knowledge | `recall_team` |

## Rules

1. **Don't call `get_session_context` on every prompt** — inject already loaded it. Only call it when you need full detail.

2. **Before reading any file**, call `summarize_file` first. Then `read_file_range` for just the section you need. Saves 90%+ tokens.

3. **Call `store_learning`** any time you discover something non-obvious mid-task. Don't wait for the hook.

4. **Call `recall_learnings`** before starting a task to check if it's been done before.

## Example flow

```
Session start:
  inject runs → LEARNINGS from past sessions in context

User asks something → you answer
  agora-summarize-interaction fires:
    → "Explained SQLite overflow: fillInCell() chains pages via 4-byte pointers" → store_learning
    → stop
  agora-auto-checkpoint fires (shell, free)

New session next day:
  inject runs → that one-sentence summary is in LEARNINGS
  Feels like continuation — ~200 tokens not 10k
```

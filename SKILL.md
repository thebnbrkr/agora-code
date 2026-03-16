# agora-code — MCP Tools Reference

> **Claude Code users:** hooks handle everything automatically after `agora-code install-hooks --claude-code`.
> This file documents the MCP server tools for **Cursor / Claude Desktop** users.

## MCP server setup

```bash
agora-code memory-server   # starts JSON-RPC server on stdio
```

Add to your editor's MCP config:
```json
{"mcpServers": {"agora-code": {"command": "agora-code", "args": ["memory-server"]}}}
```

## MCP tool reference

| Tool | When to use |
|---|---|
| `get_session_context` | Start of every chat — loads last checkpoint, recent learnings, git state |
| `save_checkpoint` | After completing a meaningful step |
| `store_learning` | Non-obvious finding: bug, gotcha, architectural decision |
| `recall_learnings` | Before starting something — check if it was solved before |
| `get_file_symbols` | Get all indexed functions/classes for a file with line numbers + code blocks |
| `search_symbols` | Search across all indexed symbols by name or description |
| `recall_file_history` | See what changed in a file across past sessions |
| `complete_session` | Archive session to long-term memory when done |
| `list_sessions` | Find past sessions |
| `get_memory_stats` | DB usage stats |

## MCP session lifecycle

```
Start   → get_session_context()       # loads last checkpoint
Working → save_checkpoint(...)        # periodic saves
Finding → store_learning(...)         # non-obvious discoveries
Done    → complete_session(...)       # archive
```

## Notes

- All queries are isolated by `project_id` (git remote URL) and `branch`
- `get_file_symbols` auto-indexes if not cached — returns `symbol_name`, `start_line`, `end_line`, `signature`, `note`, `code_block`
- `search_symbols` uses FTS5 BM25 — supports multi-word queries across name + signature + note
- `store_learning` confidence levels: `confirmed`, `likely`, `hypothesis`

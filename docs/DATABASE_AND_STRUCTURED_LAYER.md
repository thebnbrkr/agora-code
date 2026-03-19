# Database structure and structured inject layer

## When does the AI read from the DB vs the file?

| What the AI needs | Source | When |
|-------------------|--------|------|
| **Session context (goal, hypothesis, last steps)** | **DB** | `inject` / `get_session_context` — always from DB (and session.json). |
| **Relevant learnings** | **DB** | Recall/search at prompt time. |
| **File structure summary (outline of a large file)** | **DB if cached** | Pre-read hook runs `agora-code summarize`. If we have a `file_snapshot` for that path at the **same git commit**, we serve it from DB and **do not read the file**. Otherwise we read from disk and summarize. |
| **List of functions/classes in a file (names + line numbers)** | **DB first** | `get_file_symbols` looks up `symbol_notes`; if not indexed, we read the file and call `index_file`. We do **not** send the stored code body (truncated at 120 lines) to the AI — only names and lines. |
| **Full file or full function body** | **File (disk)** | The AI uses the **Read** tool (path + offset + limit). That always reads from disk, so it gets the full content. We never serve full file/function content from the DB to the AI. |
| **Edits** | **File (disk)** | All edits go to the real file; we never edit the DB. Hooks then update the DB (track-diff, index). |

So: **DB** = session state, learnings, file/symbol metadata and cached summaries. **File** = actual file content and full function bodies when the AI reads or edits.

---

## When do we use the DB instead of reading the file? (implementation detail)

| Scenario | Use DB? | What happens |
|----------|--------|--------------|
| **Pre-read hook (large file)** | **Yes when cached** | `agora-code summarize` checks the DB first: if we have a `file_snapshot` for that file at the **same git commit**, we serve that summary and do **not** read from disk. Otherwise we summarize from disk and the on-read hook then writes to the DB. |
| **Symbol list (e.g. get_file_symbols MCP)** | **Yes** | We look up `symbol_notes` in the DB first; only if not indexed do we read the file and call `index_file`. |
| **Inject (session start)** | **Yes** | We build context from the DB: last checkpoint (learnings), recent learnings, and symbol index for dirty files — plus live git log and uncommitted list. |

We do **not** serve the per-symbol `code_block` (truncated at 120 lines) to the AI in inject or `get_file_symbols` — those only return names and line numbers; the AI uses Read (disk) for full content.

**Keeping the DB in sync:** On every file **edit**, the hooks call `index_file` (or `agora-code index`) so `file_snapshots` and `symbol_notes` are updated. **You do not need to commit** — we update the AST on every edit (we read the current file from disk and write to the DB). The stored `commit_sha` is current HEAD at index time; we do **not** store an "uncommitted" flag on snapshots or symbol_notes.

**file_changes: we track both.** When we record a diff we store: `status = 'uncommitted'`, `commit_sha` = current HEAD, and **`recorded_at_commit_sha`** = same (HEAD when we recorded). We never update `recorded_at_commit_sha`. When a commit is made, the post-commit flow runs `tag_committed_files()`, which **updates** only `commit_sha` (to the new commit), `status` (to `'committed'`), and `committed_at`. So we keep both: “recorded when HEAD was X” (`recorded_at_commit_sha`) and “included in commit Y” (`commit_sha` after tag). This does not change the table in a breaking way: we added one nullable column; existing rows get `recorded_at_commit_sha = NULL`.

**When a commit is made:** The post-commit flow (e.g. `agora-code track-diff` after `git commit`, or Claude Code `on-bash.sh` when it detects a commit) calls `tag_commit()` → `tag_committed_files()`. That (1) marks **file_changes** rows for those paths as `status='committed'` and sets their `commit_sha` to the new commit (and leaves `recorded_at_commit_sha` unchanged), and (2) updates **symbol_notes** and **file_snapshots** for those paths so their `commit_sha` is set to the new commit. So after a commit, the DB reflects the new SHA and the summarize cache stays valid without re-reading the file.

---

## CLI commands to see every operation (no SQL)

Use these to inspect the DB without writing SQL:

| To see | Command |
|--------|---------|
| **DB path + counts + everything in one view** | `agora-code memory` or `agora-code memory 20` (sessions, learnings, snapshots, symbols). Add `--verbose` to see stored AST/code blocks. |
| **Sessions** | `agora-code list-sessions` or `agora-code restore` (no arg) |
| **Learnings** | `agora-code list-learnings` or `agora-code recall ""` |
| **File snapshots (AST)** | `agora-code list-snapshots` |
| **Symbol notes (functions/classes)** | `agora-code list-symbols` or `agora-code list-symbols --file path/to/file.py` |
| **File changes** | `agora-code list-file-changes` (recent across project) or `agora-code file-history <path>` (per file) |
| **API calls** | `agora-code list-api-calls` |
| **Current session + stats** | `agora-code status` |

So: **sessions** → `list-sessions` / `restore`; **learnings** → `list-learnings` / `recall`; **file_snapshots** → `list-snapshots`; **symbol_notes** → `list-symbols`; **file_changes** → `list-file-changes` / `file-history`; **api_calls** → `list-api-calls`.

---

## Where is the DB? Why didn’t I see it?

- **On your machine**: The DB is at **`~/.agora-code/memory.db`** (or whatever `AGORA_CODE_DB` is set to). To see the path and that it’s in use, run **`agora-code status`** — it prints the DB path and row counts. So the data is in that file; you don’t need SQL to “see” it — use the list-* commands above.
- **In a sandbox/CI**: If the process can’t write to `~/.agora-code/`, you get “unable to open database file”. Then set **`AGORA_CODE_DB`** to a path you can write (e.g. `$(pwd)/.agora-code/memory.db`). That’s why the test used that; on your own machine you usually don’t need it.

---

## Verify everything (no SQL)

Run these in order to confirm the whole flow without touching the DB directly:

```bash
# 1. Where is the DB and how many rows?
agora-code status

# 2. One-shot dump: path, counts, recent sessions, learnings, snapshots, symbols
agora-code memory
agora-code memory --verbose    # include stored AST/code block sample

# 3. Per-table view (no SQL)
agora-code list-sessions
agora-code list-learnings
agora-code list-snapshots
agora-code list-symbols
agora-code list-symbols --file agora_code/cli.py
agora-code list-file-changes
agora-code file-history agora_code/session.py
agora-code list-api-calls

# 4. Track diffs (single file or all uncommitted)
agora-code track-diff path/to/file.py
agora-code track-diff --all

# 5. Index a file → then summarize (should say "served from DB" when commit matches)
agora-code index agora_code/session.py
agora-code list-snapshots -n 3
agora-code summarize agora_code/session.py
```

If `status` shows a path and counts, and `memory` / list-* show data (or “No … in DB yet”), the pipeline is working. No SQL required.

---

## View vs edit: DB vs real file

- **Viewing a file or function**: We can get a **summary** or **symbol list** from the DB (cached AST, or `get_file_symbols` with names + lines). For a **full file** or **exact lines**, the Read tool reads the **real file on disk** (or we serve a summary from the DB when `summarize` uses the cache). So: view = DB when we have cache (summary/symbols) or disk (full content).
- **Editing**: We **always edit the real file on disk** (Write, SearchReplace, etc.). We never edit the DB. After you save, the hooks run **track-diff** and **index** so the DB (file_changes, file_snapshots, symbol_notes) is updated. So: edit = real file only; DB is updated by hooks after the edit.

---

## .mdc instructions (Cursor)

**Yes.** This repo uses **`.cursor/rules/agora.mdc`** for Cursor: it’s an always-applied rule with the tool reference (inject, summarize, learn, recall, checkpoint, status). So the agent gets those instructions in Cursor without you pasting them. There is no separate “.mdc instruction” for the DB layout; the DB is used by those tools under the hood.

---

## DB location and scope

- **Path**: `AGORA_CODE_DB` env var, or default `~/.agora-code/memory.db`.
- **Scope**: All tables are queried by `project_id` (and often `branch`) so sessions/learnings/snapshots are per-project.

---

## File paths: no hardcoded paths in code (DB stores resolved paths)

We **do not** hardcode machine-specific paths (e.g. `/Users/jane/...`, `C:\...`) in source code. We use:

- **Home dir**: `Path.home() / ".agora-code"` for default DB and global session.
- **Temp**: `/tmp` only for hook temp files (e.g. `mktemp /tmp/agora_hook_XXXXXX`, `/tmp/agora-code-summaries`).
- **README/config**: Placeholders like `/full/path/from/which/agora-code/agora-code` for the user to replace.

**DB path storage:** When we index or summarize, we resolve the given `file_path` to an **absolute** path and store that in `file_snapshots` and `symbol_notes`. So the DB can contain machine-specific paths (e.g. `/Users/alice/repo/foo.py`). If you copy the DB to another machine, lookups by path may not match until you re-index from that machine. We do not currently normalize to project-relative paths; that could be added later for portability.

---

## Table structure (one-sentence each)

| Table | One-sentence purpose | Queried by |
|-------|----------------------|------------|
| **sessions** | Stores one row per coding session (goal, hypothesis, status, full session_data JSON); used for restore and semantic recall. | `project_id`, `last_active`; optional vector search on embedding. |
| **learnings** | Searchable findings/decisions/blockers/next_steps and checkpoint blobs (goal, decisions, next_steps, blockers) from past convos. | `project_id`, `type`, FTS5 on finding/evidence/tags; optional vector search. |
| **file_changes** | Per-file diff summaries; `status` uncommitted→committed; `recorded_at_commit_sha` = HEAD when recorded (fixed); `commit_sha` = updated to new commit on tag. | `project_id`, `file_path`, `status`, `commit_sha`, `recorded_at_commit_sha`, `timestamp`. |
| **file_snapshots** | One row per (project, file, branch): full AST summary text + symbols JSON from tree-sitter when the file was read/edited. | `project_id`, `file_path`, `branch`; FTS5 on file_path/summary/symbols. |
| **symbol_notes** | One row per function/class/method: signature, docstring line, start/end line, and **code_block** (actual source lines). | `project_id`, `file_path`, `branch`; FTS5 on symbol_name/signature/note. |
| **api_calls** | Log of HTTP calls (method, path, status, latency) for API testing sessions. | `session_id`, path/method/success. |

---

## Main query patterns

- **Sessions**: List by `project_id` ORDER BY `last_active` DESC; load one by `session_id`. Optional: vector search on `sessions_vec_{dim}` for semantic session recall.
- **Learnings**: Keyword search via `learnings_fts` (FTS5); filter by `project_id`, `type`; checkpoint = learnings with tag `checkpoint`, evidence = JSON with goal/decisions/next_steps/blockers.
- **File snapshots**: Lookup by `(file_path, project_id, branch)`; search via `file_snapshots_fts`.
- **Symbol notes**: Lookup by `(file_path, project_id, branch)` via `get_symbols_for_file`; search via `symbol_notes_fts`.
- **File changes**: By `project_id`, `file_path`, or `commit_sha` for history.

---

## Structured layer from previous convos → new convo

**Yes.** We have a **structured data layer** that is injected into a new conversation:

1. **Last checkpoint** — From `learnings` where `tags LIKE '%checkpoint%'`, evidence JSON decoded to: goal, decisions, next_steps, blockers, files_touched, branch, commit_sha. Injected as a short "LAST CHECKPOINT" block.
2. **Recent learnings** — Non-checkpoint learnings (type = decision / finding / blocker / next_step) from `learnings`, ordered by timestamp, limited to a few. Injected as "LEARNINGS" with one line per finding.
3. **Git log** — Live `git log --oneline -6` (not from DB).
4. **Uncommitted files** — Live `git diff --name-only` (not from DB).
5. **Symbol index** — For the first few uncommitted files, we look up `symbol_notes` in the DB and inject "SYMBOL INDEX" with `file: name:line` one-liners so the agent can do targeted reads.

This is built in `session._build_recalled_context()` and printed by `agora-code inject` (and by the SessionStart hook). So **previous convos contribute**: checkpoint + learnings + symbol index from the DB; git state is live. There is no separate “conversation transcript” table; the structure is checkpoint + learnings + symbols, not raw chat history.

---

## Summary

- **Reads**: We do **not** serve file content or pre-read summary from the DB; we summarize from disk. Symbol lists are served from the DB when present.
- **DB**: One SQLite DB with sessions, learnings, file_changes, file_snapshots, symbol_notes, api_calls; all scoped by `project_id` (and often branch); FTS5 (and optional sqlite-vec) for search.
- **Structured layer**: Yes — inject is a structured mix of last checkpoint (from learnings), recent learnings, live git state, and symbol index from the DB for dirty files.

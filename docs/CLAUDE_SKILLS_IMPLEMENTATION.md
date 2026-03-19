# Implementation backlog — everything discussed

Single doc for all planned work: Claude Code skills, DB improvements, logging, context savings, hooks, and cross-platform instructions. Items are ordered by theme; checkboxes are for implementation tracking.

**DB read vs write:** `inject` only reads from the DB; only `checkpoint`, `learn`, on-stop, indexing (read/edit hooks), and `track-diff` write to it.

**Claude Code hooks — path audit:** No machine-specific paths are hardcoded. Hooks use: `mktemp /tmp/agora_hook_XXXXXX` (standard temp); `shutil.which("agora-code") or "agora-code"` for the binary; `os.environ.get("AGORA_CODE_DB", "~/.agora-code/memory.db")` and `os.path.expanduser()` for the DB; `Path.home() / ".claude" / "projects" / encoded` for transcript discovery (built from git root). Hook commands in `.claude/settings.json` reference `.claude/hooks/*.sh` (relative to project). No `/Users/...` or fixed install paths.

---

## Tracer (clarification)

**Are we using tracer in cli.py?**  
**No.** The agora-code codebase does not use Agora’s tracer/traceloop or any other tracer. The only “trace” reference is Python’s `traceback` in `log.py` for exception formatting. No tracer integration in `cli.py` or elsewhere is planned unless we explicitly add it later.

---

## 1. Claude Code skills + .claude/CLAUDE.md

**Goal**: Work in any project with hooks + instructions without requiring repo-root CLAUDE.md.

- **Skill name**: `agora-code` (not “agora-workflow”).
- **Paths**:
  - `.claude/skills/agora-code/SKILL.md` — full workflow (inject, summarize, recall, learn, checkpoint, offset/limit); loaded when relevant.
  - `.claude/CLAUDE.md` — 3–4 line reminder: “Use the agora-code skill at session start and before large file reads.”
- **Install**: `install-hooks --claude-code` creates both; optionally respect `--force` for overwrite.
- **Bundle**: Template in package (e.g. `agora_code/claude_skill.md`) copied to `.claude/skills/agora-code/SKILL.md`; short constant for `.claude/CLAUDE.md`.

**Checklist**:
- [ ] Add bundled SKILL.md template (frontmatter `name: agora-code`, description, body = current instructions + offset/limit).
- [ ] Add `.claude/CLAUDE.md` template (short reminder).
- [ ] In `_install_claude_code_hooks()`: create `.claude/skills/agora-code/`, write SKILL.md and `.claude/CLAUDE.md`.
- [ ] Document in README; optional test for created files.

---

## 2. DB: persist summary/savings events

**Goal**: Store every “context shrink” (pre-read summarization, and optionally LLM fallback) so we can report “savings in KB” and history.

- **Schema** (new table, e.g. in `memory.db`):  
  `summary_events`: `id`, `path`, `ts`, `original_tokens`, `summary_tokens`, `original_bytes`, `summary_bytes`, `parser` (ast|treesitter|generic), `project_id`, `branch`, optional `session_id`.
- **Write from**: Pre-read hook path (or summarizer) whenever we substitute a read with a summary; optionally one row when we serve full file + “[STRUCTURAL ANALYSIS NEEDED]”.
- **Decision**: New table in existing `memory.db` vs separate DB — recommend **same DB, new table** unless we want a dedicated “logs only” DB later.

**Checklist**:
- [ ] Add `summary_events` table in vector_store (or dedicated migration).
- [ ] Expose an API to insert one event (path, tokens, bytes, parser, project_id, branch, …).
- [ ] Call that from the place that performs summarization (hook or code that the hook invokes).
- [ ] (Optional) Query helpers: by project, by time range, totals.

---

## 3. Logging

**Current state**: `agora_code/log.py` — module logger, `configure()` from env, stderr handler, optional SQLite handler when `AGORA_LOG_DB=1` (writes to `logs` table in `memory.db`).

**Possible improvements** (if we want them later):
- Ensure all “silent” failure points use `log.warning` / `log.exception` instead of bare `except: pass`.
- Document `AGORA_LOG_LEVEL` and `AGORA_LOG_DB` in README.
- Optional: separate “log DB” path (e.g. `AGORA_LOG_DB_PATH`) if we ever want logs in a different file from `memory.db`.

**Checklist**:
- [ ] Audit remaining silent catches; replace with log where appropriate.
- [ ] Document logging env vars in README (and optionally in `--help` or docs).

---

## 4. Context savings in KB — CLI and reporting

**Goal**: User can see “savings” similar to Context Mode: totals and optionally history (last N, by project).

- **Data source**: Table from section 2 (`summary_events`).
- **CLI**: e.g. `agora-code stats --savings` or `agora-code summary-stats`:
  - Totals: sum of `original_tokens` vs `summary_tokens`, `original_bytes` vs `summary_bytes`, ratio / % reduction.
  - Optional: last N events, by project_id, by path.
- **Output**: Human-readable (and optionally JSON for scripts); e.g. “Total saved: X KB (Y% reduction); last 10: …”.

**Checklist**:
- [ ] Implement `stats --savings` or `summary-stats` subcommand that reads `summary_events`.
- [ ] Format: table or summary line + optional history.
- [ ] (Optional) MCP tool that returns the same report for in-chat display.

---

## 5. Gemini pre-read: offset/limit bypass

**Goal**: On Gemini, a targeted read (with `offset`/`limit`) should pass through and return real file content, like Cursor’s pre-read.

- **Current**: Cursor’s pre-read checks `tool_input` for `offset` or `limit` and bypasses summarization. Gemini’s `.gemini/hooks/pre-read.sh` does not.
- **Change**: In `.gemini/hooks/pre-read.sh` (or the script it calls), if the tool input has `offset` or `limit`, skip summarization and allow the read (e.g. output allow / do not replace with summary).

**Checklist**:
- [ ] Add offset/limit check to Gemini pre-read flow; bypass summarization when present.
- [ ] Ensure install/package provides the updated script (or document manual edit).

---

## 6. Cursor: global rule for cross-project

**Goal**: One set of agora-code instructions applies to all projects without per-project files.

- **Mechanism**: Install a rule into **user global** `~/.cursor/rules/` (e.g. `agora.mdc`) with the same content as the skill / CLAUDE reminder (or a short pointer to it).
- **CLI**: e.g. `install-hooks --cursor-global` (or flag) that writes/copies to `~/.cursor/rules/agora.mdc`.

**Checklist**:
- [ ] Add option to write to `~/.cursor/rules/agora.mdc` (content = skill summary or short instructions).
- [ ] Document “cross-project Cursor” in README.

---

## 7. E2E and optional features (deferred)

- **E2E**: Tests for Claude Code / Gemini hooks and optional features (e.g. pre-read, inject, summary-stats). Coverage can grow over time; logs + manual checks are a minimum.
- **Optional**: Separate DB only for “logs” if we later want to isolate audit/telemetry from main memory DB; not required for current scope.

---

## 8. Summary table

| Area | What | Status |
|------|------|--------|
| Tracer | Not used in cli.py or elsewhere | Confirmed |
| Claude skills | `.claude/skills/agora-code/SKILL.md` + `.claude/CLAUDE.md` | Spec’d; not implemented |
| DB | `summary_events` table, write from summarization path | Spec’d; not implemented |
| Logging | Already in place; audit + docs | Partial |
| Savings CLI | `stats --savings` or `summary-stats` from `summary_events` | Spec’d; not implemented |
| Gemini | Offset/limit bypass in pre-read | Spec’d; not implemented |
| Cursor global | `~/.cursor/rules/agora.mdc` install option | Spec’d; not implemented |

This file is the single place for “everything discussed so far” and can be updated as items are done or new ones added.

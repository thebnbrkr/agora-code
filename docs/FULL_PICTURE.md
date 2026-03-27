# agora-code — full picture

A reference doc mapping every CLI command to its DB table, what's actually used,
what's legacy, and what we're leaving on the table.

---

## 1. The DB — 7 tables, everything scoped by project_id

```
~/.agora-code/memory.db  (SQLite + FTS5 + optional sqlite-vec)

┌──────────────────────┬─────────────────────────────────────────────────────────┐
│ Table                │ What it stores                                          │
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ sessions             │ One row per coding session. goal, status, compressed    │
│                      │ transcript JSON, optional embedding for semantic recall. │
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ learnings            │ Findings, decisions, blockers, next_steps, checkpoints. │
│                      │ FTS5 for keyword search, sqlite-vec for semantic.       │
│                      │ Checkpoints: tag=checkpoint, evidence=JSON blob.        │
│                      │ Commit-tagged: commit_sha + link in commit_learnings.   │
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ commit_learnings     │ Junction table: (commit_sha, learning_id, project_id).  │
│                      │ Created when learn-from-commit runs after a git commit. │
│                      │ Used by inject to fetch learnings for recent commits.   │
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ file_changes         │ Per-file diff summaries. status=uncommitted→committed.  │
│                      │ recorded_at_commit_sha = HEAD when recorded (fixed).    │
│                      │ commit_sha = updated to new commit on tag_commit.       │
│                      │ #kept/#not_kept tag: auto-appended on commit if missing.│
│                      │ diff_snippet: raw +/- lines from git diff (not summary).│
│                      │ diff_summary: LLM/regex 1-line summary of what changed. │
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ file_snapshots       │ One row per (project, file, branch). AST summary text  │
│                      │ + symbols JSON. Cache key = file_path + commit_sha.    │
│                      │ Served by summarize when commit matches — zero disk.   │
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ symbol_notes         │ One row per function/class. signature, start_line,     │
│                      │ end_line, code_block (120 lines max). FTS5 searchable. │
│                      │ Only injected as name:line — code_block never auto-sent│
├──────────────────────┼─────────────────────────────────────────────────────────┤
│ api_calls            │ HTTP call log (method, path, status, latency).         │
│                      │ Written by serve/chat (original API agent product).    │
│                      │ Never read by inject, recall, or any Claude Code hook. │
└──────────────────────┴─────────────────────────────────────────────────────────┘
```

---

## 2. How inject builds context (what Claude sees at session start)

```
agora-code inject
        │
        ├── learnings WHERE project_id=X AND tags LIKE '%checkpoint%'
        │   ORDER BY timestamp DESC LIMIT 1
        │   → LAST CHECKPOINT block (goal, decisions, next_steps, blockers)
        │
        ├── sessions WHERE project_id=X ORDER BY last_active DESC LIMIT 1
        │   → LAST SESSION (compressed) — last 10 exchanges
        │
        ├── commit_learnings JOIN learnings for last 3 branch commits + 1 main
        │   (up to 4 commit SHAs total via get_learnings_for_commits)
        │   + fallback: learnings WHERE project_id=X recent non-checkpoint
        │   to fill remaining budget (AGORA_INJECT_LEARNINGS_K, default 6)
        │   → LEARNINGS block (type-tagged findings)
        │
        ├── git log --oneline -6   (live, not from DB)
        │   → GIT LOG block
        │
        ├── git diff --name-only   (live)
        │   → UNCOMMITTED block
        │
        └── symbol_notes WHERE file_path IN dirty_files AND project_id=X
            → SYMBOL INDEX block (name:line for dirty files only)
```

**Gap:** Symbol index only covers dirty (uncommitted) files.
Clean files that were read in a past session have symbols in the DB but
they're never injected. You miss context on stable files you've worked with.

---

## 3. Every CLI command — what it does, which table, what layer

### Used by SKILL.md (active Claude Code flow)

| Command | DB table(s) touched | What it does |
|---|---|---|
| `inject` | learnings, sessions, symbol_notes | Prints compressed context for session start |
| `summarize <file>` | file_snapshots (read + write) | AST-compresses file; serves from DB if commit matches |
| `checkpoint` | learnings (write, tag=checkpoint) | Saves goal/decisions/next_steps/blockers mid-task |
| `complete` | sessions (write), learnings (write) | Archives session to long-term memory |
| `learn "<text>"` | learnings (write) | Force-stores a finding now |
| `recall "<query>"` | learnings (read, FTS5→semantic) | BM25 search, falls back to semantic if OPENAI_API_KEY set |
| `status -p` | all tables (counts) | Shows DB path, row counts, current session state |
| `install-hooks` | — | Writes .claude/settings.json + all hook scripts |
| `memory-server` | all tables | Starts MCP server Claude Code connects to |

### Mentioned in CLAUDE.md but NOT in SKILL.md

| Command | DB table(s) touched | What it does | Value |
|---|---|---|---|
| `track-diff <file> --note "..."` | file_changes (write) | Stores git diff + your note about what changed and why | High — lets future inject show what you changed. Missing from SKILL.md means users skip it |

### Inspection/debug — not directed by skill, but useful

| Command | DB table(s) touched | What it does | When useful |
|---|---|---|---|
| `memory` | all tables (read) | DB path + counts + recent dump of everything | Health check — verify hooks are writing |
| `list-sessions` | sessions | Recent sessions (lightweight) | See past session goals |
| `list-learnings` | learnings | Recent learnings dump | Verify what's been stored |
| `list-snapshots` | file_snapshots | Recent AST summaries | Verify summarize cache is populated |
| `list-symbols` | symbol_notes | Functions/classes indexed | Verify symbol index is working |
| `list-file-changes` | file_changes | Recent tracked diffs | See what was recorded |
| `file-history <file>` | file_changes | Change history for one file | Understand how a file evolved |
| `commit-log` | learnings + file_changes | Learnings per commit | See what was learned at each commit |
| `notes` | file_changes | AI-written change notes per file | Summary of what changed and why |
| `index <file>` | file_snapshots, symbol_notes (write) | Force re-indexes a file | Pre-populate symbol index before a session |
| `restore` | sessions | Restore a past session as active | Resume a specific session |
| `remove <id>` | learnings | Delete a learning by ID | Clean up bad learnings |
| `show` | sessions | Everything in current session context | Debug what Claude sees right now |

### Original API agent product — functional, different use case

These are the original agora-code purpose: turn any REST API into a
memory-aware agent. Fully functional, not broken. Just not part of the
Claude Code session memory flow.

| Command | DB table(s) touched | What it does |
|---|---|---|
| `scan` | — | Crawl codebase/URL, discover all API routes via AST/OpenAPI/regex |
| `agentify` | — | Auto-generate LLM workflows from scanned API routes |
| `auth` | — | Store API auth credentials (token, type) |
| `chat` | sessions, api_calls | Interactive NL chat against a live API |
| `serve` | api_calls (writes), sessions | Start MCP server exposing API routes as tools. Uses vector_store for memory. |
| `stats` | api_calls | API call stats, failure patterns from memory |
| `list-api-calls` | api_calls | Raw API call log dump |

`serve` is NOT the same as `memory-server`. `serve` exposes a scanned API
as MCP tools (plug into Claude Desktop). `memory-server` exposes the
agora-code DB (sessions, learnings, symbols) as MCP tools for Claude Code.
The `api_calls` table is written by `serve`/`chat` and read by `stats` —
it is never read by inject, recall, or any session memory hook.

---

## 4. Hook → DB write path (what happens automatically)

```
User edits file
  → on-edit.sh fires (PostToolUse Write|Edit)
      → agora-code track-diff <file>       ← automatic, no --note (mechanical diff only)
          → file_changes: insert diff summary
            diff_summary = LLM summary of git diff (GPT-4o-mini if OPENAI_API_KEY) → regex fallback
            --note "..." adds your explanation; without it, only the diff mechanics are stored
      → index_file(file_path, project_id, branch)    ← called directly, not via CLI
          → file_snapshots: upsert AST summary
          → symbol_notes: delete old, insert new (functions/classes)

User reads file
  → pre-read.sh fires (PreToolUse Read)
      → agora-code summarize <file>
          → file_snapshots: read cache (serve from DB if commit matches)
          → file_snapshots: write new summary if not cached
  → on-read.sh fires (PostToolUse Read)
      → agora-code index <file>
          → file_snapshots: upsert
          → symbol_notes: upsert

User runs git commit
  → on-bash.sh fires (PostToolUse Bash, detects 'git commit')
      → tag_commit(sha, files)
          → file_changes: status uncommitted→committed, commit_sha updated
            #kept auto-appended to diff_summary IF no #kept or #not_kept tag
            (if user ran track-diff --note "... #not_kept", that tag stays)
          → symbol_notes: commit_sha updated
          → file_snapshots: commit_sha updated
      → learn-from-commit sha
          → learnings: writes diff-based learnings tagged with commit_sha
          → commit_learnings: links learning_id → commit_sha

#kept / #not_kept explained:
  - track-diff --note "tried X #not_kept" → saved in file_changes.diff_summary
  - On git commit: tag_committed_files() checks — if no tag present, appends #kept
  - If already has #not_kept, leaves it alone (you marked it as reverted)
  - Result: every file_change row ends up tagged so notes/file-history shows
    whether a change was actually committed or thrown away

Claude stops
  → on-stop.sh fires (Stop)
      → agora-code checkpoint --quiet
          → learnings: writes checkpoint (tag=checkpoint, evidence=JSON)

User submits prompt
  → on-prompt.sh fires (UserPromptSubmit)
      → agora-code recall "<prompt>"
          → learnings: FTS5 search, injects relevant findings as context
```

---

## 5. What we're leaving on the table

### a) track-diff not in SKILL.md
`track-diff` is the only way file_changes gets written during a session.
Without it: no diff history, no `notes` command output, no context in future
sessions about what changed. CLAUDE.md mentions it, SKILL.md doesn't.
**Fix:** Add to SKILL.md rules.

### b) Symbol index only covers dirty files
inject injects symbol index (name:line) only for uncommitted files.
Clean files that were read and indexed in past sessions have full symbol
data in the DB but it never appears in inject.
**Opportunity:** inject could include symbols for recently-read clean files too,
not just dirty ones. Would give Claude function:line context without re-reading.

### c) summarize cache not pre-populated
The summarize cache (file_snapshots) only gets populated when a file is
actually read. For a new session on a large codebase, the first read of
every file hits disk. `agora-code index <dir>` could pre-populate everything.
**Opportunity:** `install-hooks` or a setup command could pre-index common files.

### d) track-diff --note richness is never enforced
on-edit.sh auto-runs `track-diff <file>` (no --note) after every edit.
The mechanical diff is always captured automatically.
What's NOT captured: your explanation of what changed and why.
The CLAUDE.md rule says to run `track-diff <file> --note "..."` after every
edit — that note is the richest signal (intent, dependencies, outcome).
Without it, file_changes.diff_summary has only the LLM-summarized diff or
regex fallback, never your explanation.
**Opportunity:** on-edit.sh could prompt Claude to emit a track-diff with --note,
or on-stop.sh could enrich diff_summary with explanation from the transcript.

### e) Recall BM25 limitation
Without OPENAI_API_KEY, recall is keyword-only. "vectorized DataChunk" won't
match "execution engine" even though they're related. The OR project_id IS NULL
bug also shows cross-project learnings. Both degrade recall quality.

**Fix needed:** search_learnings_keyword uses `(project_id = ? OR project_id IS NULL)`.
Old learnings with NULL project_id bleed into every project's recall.
Should be strict `project_id = ?` — but requires migrating/deleting NULL-project rows.

---

## 6. /agora-code — what the skill command actually injects

When a user types `/agora-code` in Claude Code, they get the **SKILL.md text
injected into Claude's context** — nothing more.

```
/agora-code
  → reads ~/.claude/skills/agora-code/SKILL.md   (installed by install-hooks)
  → injects the full text into Claude's system context
  → Claude now knows the rules, hooks table, and lifecycle
```

This is NOT the same as `agora-code inject`. The skill command injects rules.
`agora-code inject` queries the DB and injects session data (checkpoints, learnings, symbols).

**Both are needed at session start:**
1. `/agora-code` → Claude learns the rules (SKILL.md text)
2. `agora-code inject` → Claude gets the actual session memory (DB data)

**SKILL.md source of truth:**
- `agora_code/SKILL.md` — ships inside pip package, deployed to users
- `_get_skill_md_content()` in cli.py tries this first, falls back to `.claude/skills/agora-code/SKILL.md`
- `install-hooks` copies it to `~/.claude/skills/agora-code/SKILL.md` on the user's machine

---

## 7. docs/ — for humans, never injected

`docs/` files are never read by hooks, inject, or any automatic process.
They're reference documentation for contributors. Nothing in SKILL.md,
CLAUDE.md, or any hook reads from `docs/`. If a user opens Claude Code in
this repo and runs /agora-code, they get SKILL.md — not docs/.

---

## 8. Wrong assumptions made during architectural work (lessons learned)

These are concrete mistakes made when reasoning about the codebase without reading first.
Kept here as a reminder to verify before claiming.

| Claim | Reality | How to verify |
|---|---|---|
| "on-edit.sh only calls index, not track-diff" | on-edit.sh calls BOTH track-diff AND index_file (lines 29, 34-37) | Read on-edit.sh |
| "track-diff only runs if CLAUDE.md rule followed" | track-diff runs automatically after every edit on code files | on-edit.sh line 29 |
| "inject uses last 3 commits" | inject uses up to 4 SHAs: 3 branch commits + 1 main (via get_learnings_for_commits) | Read inject command in cli.py |
| "API commands (scan, agentify, chat, serve) are dead code" | serve/chat are the original product — fully functional REST API agent | Read cli.py scan/serve sections |
| "6 tables in the DB" | 7 tables — commit_learnings is a junction table, not part of learnings | Read models.py |
| "SubagentStart can block the subagent" | SubagentStart fires AFTER launch, exit code ignored for blocking. Only PreToolUse can block | Claude Code hooks docs |
| "on-subagent.sh stdout injects into subagent context" | SubagentStart stdout shows to user only; must use JSON hookSpecificOutput.additionalContext | Claude Code hooks docs |
| "/agora-code runs agora-code inject" | /agora-code injects SKILL.md text (rules), not DB data. inject is separate | Skill mechanism docs |
| "search_learnings_keyword is project-scoped" | Uses (project_id = ? OR project_id IS NULL) — leaks old NULL-project learnings across projects | Read cli.py search_learnings_keyword |
| "_summarize_diff is just regex" | Tries LLM summary (GPT-4o-mini via OPENAI_API_KEY) first, regex is fallback | Read cli.py _summarize_diff |
| "file_changes only stores a summary, not raw diff" | file_changes has BOTH diff_summary (LLM/regex summary) AND diff_snippet (raw +/- lines from git diff) | PRAGMA table_info(file_changes) |
| "store_learning always inserts a new row" | store_learning checks (project_id, commit_sha, finding) first — returns existing id if exact duplicate | Read vector_store.py store_learning |
| "inject LEARNINGS shows the finding (diff stat)" | inject now shows evidence (commit message) as primary line — more signal, fewer tokens | Read session.py _build_recalled_context |
test line

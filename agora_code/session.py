"""
session.py — Session lifecycle manager for agora-code.

Session state is stored as JSON (not YAML, not SQLite) so it's:
  - Human-readable and debuggable
  - Easy to grep/cat from the terminal
  - Editable by hand if needed

File locations:
  - .agora-code/session.json   (project-local — take priority)
  - ~/.agora-code/session.json (global fallback)

The VectorStore (SQLite) is used separately only for:
  - Learnings (searchable knowledge base)
  - API call logs (pattern detection)

Session JSON shape:
{
  "session_id": "2026-03-08-debug-user-api",
  "started_at": "2026-03-08T09:30:00Z",
  "last_active": "2026-03-08T14:45:00Z",
  "status": "in_progress",
  "goal": "Fix 500 errors on POST /users",
  "hypothesis": "Email validation middleware too strict",
  "current_action": "Testing email formats",
  "api_base_url": "https://api.example.com",
  "endpoints_tested": [...],
  "discoveries": [...],
  "decisions_made": [...],
  "next_steps": [...],
  "blockers": [...],
  "tags": []
}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
#  File resolution                                                              #
# --------------------------------------------------------------------------- #

_AGORA_DIR  = ".agora-code"
_SESSION_FILE = "session.json"
_GLOBAL_DIR = Path.home() / ".agora-code"


def _find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """
    Walk up from start until we find .agora-code/, .git/, or pyproject.toml.
    Returns None if nothing found (e.g. MCP server spawned from /). This
    signals callers to use the global ~/.agora-code fallback.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / _AGORA_DIR).is_dir():
            return current
        if (current / ".git").is_dir():
            return current
        if (current / "pyproject.toml").is_file():
            return current
        parent = current.parent
        if parent == current:
            # Reached filesystem root — no project found
            return None
        current = parent


def get_session_path(project_root: Optional[Path] = None) -> Path:
    """Return path to the session.json for this project.
    
    Falls back to ~/.agora-code/session.json when no project root is found
    (e.g. MCP server spawned from / without a project context).
    """
    root = project_root if project_root is not None else _find_project_root()
    if root is None:
        # No project found — use global home directory fallback
        return get_global_session_path()
    return root / _AGORA_DIR / _SESSION_FILE


def get_global_session_path() -> Path:
    return _GLOBAL_DIR / _SESSION_FILE


def _resolve_session_path() -> Path:
    """Project-local wins over global."""
    local = get_session_path()
    if local.exists():
        return local
    return get_global_session_path()


# --------------------------------------------------------------------------- #
#  Git helpers                                                                 #
# --------------------------------------------------------------------------- #

def _get_git_branch() -> Optional[str]:
    """
    Return current git branch name, or None if not in a git repo
    or git is unavailable. Uses rev-parse for compatibility with
    older git versions (works even in detached HEAD — returns 'HEAD').
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        branch = result.stdout.strip()
        return branch if result.returncode == 0 and branch else None
    except Exception:
        return None


def _get_uncommitted_files() -> List[str]:
    """
    Return list of files with uncommitted changes (staged + unstaged).
    Falls back to last-commit files if the working tree is clean.
    Returns [] if not in a git repo or git unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        files: List[str] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            # Format: "XY filename" — skip the 2-char status prefix
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                fpath = parts[1].strip()
                # Handle renames: "old -> new"
                if " -> " in fpath:
                    fpath = fpath.split(" -> ")[1]
                files.append(fpath)
        # Fallback: if tree is clean, grab last commit's files
        if not files:
            r2 = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if r2.returncode == 0:
                files = [f.strip() for f in r2.stdout.splitlines() if f.strip()]
        return files
    except Exception:
        return []


def _get_commit_sha() -> Optional[str]:
    """
    Return the current HEAD commit SHA (short, 12 chars), or None if not in
    a git repo or git is unavailable. Used for true checkpoint rewind.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and sha else None
    except Exception:
        return None


def _get_git_author() -> Optional[str]:
    """
    Return the current git user identity (user.name + user.email) for
    attribution on file changes and checkpoints. Works for humans and agents.
    """
    try:
        name = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        email = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if name and email:
            return f"{name} <{email}>"
        return name or email or None
    except Exception:
        return None


def _get_project_id() -> str:
    """
    Return a stable identifier for the current project.

    Priority:
      1. Git remote origin URL (normalized — no credentials, no .git suffix)
      2. Current directory name as fallback

    Used to scope DB queries to the current project so sessions/learnings
    from unrelated projects don't bleed into context injection.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Strip credentials (https://user:pass@host → https://host)
            url = re.sub(r'https?://[^@]+@', 'https://', url)
            # Normalize trailing slash and .git suffix
            url = url.rstrip("/")
            if url.endswith(".git"):
                url = url[:-4]
            if url:
                return url
    except Exception:
        pass
    return Path.cwd().name


def _extract_ticket(branch: Optional[str]) -> Optional[str]:
    """
    Extract a ticket/issue number from a branch name.
    Handles common patterns:
      JIRA-123-fix-auth       → 'JIRA-123'
      feature/JIRA-423-login  → 'JIRA-423'
      fix/gh-456-null-ptr     → 'gh-456'
      GH-78-perf              → 'GH-78'
    Returns None if no ticket pattern found.
    """
    if not branch:
        return None
    # Match: optional prefix/, then LETTERS-digits pattern
    match = re.search(r'(?:^|[/-])([A-Z]{2,10}-\d+|gh-\d+|GH-\d+)', branch, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def _branch_to_goal(branch: Optional[str]) -> Optional[str]:
    """
    Derive a human-readable goal hint from a branch name.
    Examples:
      feat/auth-service       → 'Working on feat/auth-service'
      JIRA-423-fix-login      → 'JIRA-423: fix login'
      fix/null-pointer-error  → 'Working on fix/null-pointer-error'
    """
    if not branch or branch in ('HEAD', 'main', 'master', 'develop'):
        return None
    ticket = _extract_ticket(branch)
    if ticket:
        # Strip ticket prefix to get description
        desc = re.sub(r'(?i)^[A-Z]+-\d+-?', '', branch.split('/')[-1])
        desc = desc.replace('-', ' ').strip()
        return f"{ticket}: {desc}" if desc else ticket
    return f"Working on {branch}"


import re  # noqa: E402 — needed by _branch_to_goal, placed after helpers


# --------------------------------------------------------------------------- #
#  Session creation                                                             #
# --------------------------------------------------------------------------- #

def new_session(
    goal: Optional[str] = None,
    api_base_url: Optional[str] = None,
    tags: Optional[List[str]] = None,
    context: Optional[str] = None,   # any free-text project context
) -> Dict[str, Any]:
    """
    Create a fresh session dict.
    Does NOT save to disk — call save_session() to persist.

    Works for any dev session — API or non-API.
    API-specific fields (api_base_url, endpoints_tested) are optional
    and simply stay empty for general coding sessions.
    """
    now = _now()
    return {
        # ── identity ──────────────────────────────────────────────
        "session_id":      _slug(goal=goal, branch=_get_git_branch()),
        "started_at":      now,
        "last_active":     now,
        "status":          "in_progress",
        # ── git context (auto-detected) ────────────────────────────
        "branch":          _get_git_branch(),
        "commit_sha":      _get_commit_sha(),
        "ticket":          _extract_ticket(_get_git_branch()),
        "uncommitted_files": _get_uncommitted_files(),
        # ── what you're working on ─────────────────────────────────
        "goal":            goal or _branch_to_goal(_get_git_branch()) or "",
        "hypothesis":      None,
        "current_action":  None,
        "context":         context or "",        # free-text: project notes, stack info
        # ── what you found ─────────────────────────────────────────
        "discoveries":     [],
        "decisions_made":  [],
        # ── what's next ────────────────────────────────────────────
        "next_steps":      [],
        "blockers":        [],
        "tags":            tags or [],
        # ── code changes (non-API) ─────────────────────────────────
        "files_changed":   [],   # [{"file": "foo.py", "what": "added retry logic"}]
        # ── API-specific (stays empty for non-API sessions) ────────
        "api_base_url":    api_base_url or "",
        "endpoints_tested": [],
    }


# --------------------------------------------------------------------------- #
#  Save / load                                                                 #
# --------------------------------------------------------------------------- #

def save_session(
    session: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> Path:
    """
    Write session to .agora-code/session.json.
    Always updates last_active to now.
    Writes atomically (temp file + rename).
    """
    session = {**session, "last_active": _now()}

    path = get_session_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write a gitignore so session.json isn't accidentally committed
    _ensure_gitignore(path.parent)

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_session(project_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    Load the current session JSON.
    Returns None if no session file exists.
    """
    path = get_session_path(project_root)
    if not path.exists():
        # Try global path
        path = get_global_session_path()
        if not path.exists():
            return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_session_if_recent(
    max_age_hours: float = 24.0,
    project_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Load session only if last_active is within max_age_hours.
    Returns None if session is stale or missing.

    This is what MCPServer calls on startup to auto-restore context.
    """
    session = load_session(project_root)
    if not session:
        return None

    try:
        last = datetime.fromisoformat(session["last_active"])
        # Make timezone-aware if naive
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
    except Exception:
        return None

    return session


def update_session(
    updates: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Merge updates into the current session and save.
    Creates a new minimal session if none exists.
    Also auto-refreshes git branch + uncommitted files, and dual-writes
    to SQLite so every checkpoint is browsable (not just completed sessions).
    """
    existing = load_session(project_root) or new_session()
    # Auto-refresh git state on every checkpoint
    branch = _get_git_branch() or existing.get("branch")
    git_updates: Dict[str, Any] = {
        "branch":            branch,
        "commit_sha":        _get_commit_sha() or existing.get("commit_sha"),
        "ticket":            _extract_ticket(branch) or existing.get("ticket"),
        "uncommitted_files": _get_uncommitted_files() or existing.get("uncommitted_files", []),
    }
    # Auto-enrich goal from branch if not already set by user/AI
    if not existing.get("goal") and not updates.get("goal"):
        auto_goal = _branch_to_goal(branch)
        if auto_goal:
            git_updates["goal"] = auto_goal
    merged = {**existing, **git_updates, **updates}
    save_session(merged, project_root)

    # Dual-write to SQLite so sessions are always browsable
    try:
        from agora_code.vector_store import get_store
        get_store().save_session(merged, project_id=_get_project_id())
    except Exception:
        pass  # Non-fatal: JSON file is always the source of truth

    return merged


def archive_session(
    summary: Optional[str] = None,
    outcome: str = "success",
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Mark session as complete and persist to VectorStore for long-term memory.
    Also saves a summary embedding if embeddings are available.
    Returns the final session dict.
    """
    session = load_session(project_root) or {}
    session["status"] = "complete"
    session["outcome"] = outcome
    if summary:
        session["summary"] = summary
    save_session(session, project_root)

    # Persist to vector store for future recall
    try:
        from agora_code.vector_store import get_store
        from agora_code.embeddings import get_embedding

        text = _session_embedding_text(session)
        embedding = get_embedding(text)

        store = get_store()
        store.save_session(session, embedding=embedding, project_id=_get_project_id())
    except Exception:
        pass  # Non-fatal: session is still saved to JSON

    return session


# --------------------------------------------------------------------------- #
#  Endpoint tracking helpers                                                   #
# --------------------------------------------------------------------------- #

def record_endpoint_attempt(
    session: Dict[str, Any],
    *,
    method: str,
    path: str,
    success: bool,
    params: Optional[dict] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update endpoints_tested in-place for a single API call result.
    Returns the modified session (not saved — caller must call save_session).
    """
    tested = session.setdefault("endpoints_tested", [])

    # Find existing entry for this endpoint
    key = f"{method.upper()} {path}"
    entry = next((e for e in tested if f"{e['method']} {e['path']}" == key), None)

    if entry is None:
        entry = {
            "method": method.upper(),
            "path": path,
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "last_attempt": None,
            "last_error": None,
            "working_parameters": None,
            "failing_parameters": [],
        }
        tested.append(entry)

    entry["attempts"]    += 1
    entry["last_attempt"] = _now()

    if success:
        entry["successes"] += 1
        if params:
            entry["working_parameters"] = params
    else:
        entry["failures"] += 1
        entry["last_error"] = error
        if params and params not in entry["failing_parameters"]:
            entry["failing_parameters"].append(params)

    return session


def add_discovery(
    session: Dict[str, Any],
    finding: str,
    *,
    evidence: Optional[str] = None,
    confidence: str = "confirmed",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Append a discovery to the session. Returns modified session (not saved)."""
    session.setdefault("discoveries", []).append({
        "timestamp": _now(),
        "finding":   finding,
        "evidence":  evidence,
        "confidence": confidence,
        "tags":      tags or [],
    })
    return session


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(goal: Optional[str], branch: Optional[str] = None) -> str:
    """Create a readable session ID like '2026-03-08-fix-post-users'."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source = goal or branch
    if not source:
        return f"{date}-{uuid.uuid4().hex[:6]}"
    words = "".join(c if c.isalnum() else "-" for c in source.lower()).strip("-")
    words = "-".join(w for w in words.split("-") if w)[:40]
    return f"{date}-{words}"


def _session_embedding_text(session: Dict) -> str:
    """Build a text snippet to embed for semantic session search."""
    parts = [
        session.get("goal", ""),
        session.get("hypothesis", "") or "",
        session.get("summary", "") or "",
    ]
    for d in session.get("discoveries", [])[:5]:
        parts.append(d.get("finding", ""))
    return " ".join(p for p in parts if p).strip()


def _ensure_gitignore(agora_dir: Path) -> None:
    gi = agora_dir / ".gitignore"
    if not gi.exists():
        try:
            gi.write_text("# agora-code local state — do not commit\n*\n", encoding="utf-8")
        except Exception:
            pass


def _build_recalled_context(project_id: Optional[str] = None) -> Optional[str]:
    """
    Build structured inject context from DB + git.

    Output sections (most-signal-first, target ~350 tokens total):
      1. Last checkpoint  — goal, decisions, next_steps, blockers
      2. Recent learnings — type-tagged, non-checkpoint findings
      3. Git log          — last 5 commits (always fresh)
      4. Uncommitted files
      5. Symbol index     — one-liners for recently touched files
    """
    import json as _json
    import subprocess as _sp

    try:
        from agora_code.vector_store import get_store
        store = get_store()
        pid = project_id or _get_project_id()
        branch = _get_git_branch()
        parts: List[str] = []

        # ── 1. Last structured checkpoint ────────────────────────────────────
        checkpoints = store.search_learnings_keyword(
            "", k=1,
            project_id=pid,
            type="finding",      # checkpoints stored as finding type
        )
        # filter to actual checkpoint learnings by tag
        raw_checkpoints = store._conn_().execute("""
            SELECT finding, evidence FROM learnings
            WHERE (project_id = ? OR project_id IS NULL)
              AND tags LIKE '%checkpoint%'
            ORDER BY timestamp DESC LIMIT 1
        """, (pid,)).fetchone()

        if raw_checkpoints:
            finding = raw_checkpoints[0] or ""
            evidence_raw = raw_checkpoints[1] or ""
            try:
                ev = _json.loads(evidence_raw)
                cp_goal = ev.get("goal", "")
                cp_decisions = ev.get("decisions", [])
                cp_next = ev.get("next_steps", [])
                cp_blockers = ev.get("blockers", [])
                cp_files = ev.get("files_touched", [])
                cp_branch = ev.get("branch", "")
                cp_commit = ev.get("commit_sha", "")

                section = ["LAST CHECKPOINT"]
                if cp_goal:
                    section.append(f"  goal:      {cp_goal[:120]}")
                if cp_decisions:
                    section.append("  decisions: " + "; ".join(d[:80] for d in cp_decisions[:3]))
                if cp_next:
                    section.append("  next:      " + "; ".join(n[:80] for n in cp_next[:3]))
                if cp_blockers:
                    section.append("  blockers:  " + "; ".join(b[:80] for b in cp_blockers[:2]))
                if cp_files:
                    section.append("  files:     " + ", ".join(cp_files[:6]))
                if cp_branch or cp_commit:
                    section.append(f"  branch:    {cp_branch}  commit: {cp_commit}")
                parts.append("\n".join(section))
            except Exception:
                # evidence wasn't JSON — fall back to raw finding text
                if finding:
                    parts.append(f"LAST CHECKPOINT\n  {finding[:200]}")

        # ── 2. Recent non-checkpoint learnings (decisions, findings, blockers) ─
        learnings_k = int(os.environ.get("AGORA_INJECT_LEARNINGS_K", "4"))
        learnings = store._conn_().execute("""
            SELECT finding, type, confidence FROM learnings
            WHERE (project_id = ? OR project_id IS NULL)
              AND (tags NOT LIKE '%checkpoint%' OR tags IS NULL)
            ORDER BY timestamp DESC LIMIT ?
        """, (pid, learnings_k)).fetchall()

        if learnings:
            TYPE_ICON = {"decision": "→", "blocker": "!", "next_step": "»", "finding": "·"}
            section = ["LEARNINGS"]
            for lr in learnings:
                icon = TYPE_ICON.get(lr[1] or "finding", "·")
                conf = {"confirmed": "", "likely": "~", "hypothesis": "?"}.get(lr[2] or "confirmed", "")
                section.append(f"  {icon}{conf} {lr[0][:120]}")
            parts.append("\n".join(section))

        # ── 3. Git log (always live — no storage needed) ─────────────────────
        def _git(cmd):
            try:
                r = _sp.run(cmd, capture_output=True, text=True, timeout=5)
                return r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                return ""

        git_log = _git(["git", "log", "--oneline", "-6"])
        if git_log:
            parts.append(f"GIT LOG\n" + "\n".join(f"  {l}" for l in git_log.splitlines()))

        # ── 4. Uncommitted files ──────────────────────────────────────────────
        uncommitted = _git(["git", "diff", "--name-only", "HEAD"]).splitlines()
        staged = _git(["git", "diff", "--cached", "--name-only"]).splitlines()
        dirty = list(dict.fromkeys(uncommitted + staged))[:8]
        if dirty:
            parts.append("UNCOMMITTED\n  " + ", ".join(dirty))

        # ── 5. Symbol index for recently touched files ────────────────────────
        if dirty:
            symbol_lines = []
            for fp in dirty[:3]:
                syms = store.get_symbols_for_file(fp, project_id=pid, branch=branch)
                if syms:
                    names = ", ".join(
                        f"{s['symbol_name']}:{s['start_line']}" for s in syms[:8]
                    )
                    symbol_lines.append(f"  {fp}: {names}")
            if symbol_lines:
                parts.append("SYMBOL INDEX\n" + "\n".join(symbol_lines))

        return "\n\n".join(parts) if parts else None
    except Exception:
        return None

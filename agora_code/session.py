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
  "session_id": "2026-03-08-debug-user-api-3f9a1c",
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


def _commits_since(sha: Optional[str]) -> Optional[int]:
    """
    Number of commits on HEAD since `sha`. A None sha means the checkpoint was
    saved before any commit existed, so every commit on HEAD counts.
    Returns None when unknown: git unavailable, no HEAD, a non-sha value, or
    `sha` isn't an ancestor of HEAD (other branch, amended/rebased away).
    """
    if sha is not None and not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha):
        return None
    try:
        if sha is not None:
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if ancestor.returncode != 0:
                return None
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{sha}..HEAD" if sha else "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return int(result.stdout.strip()) if result.returncode == 0 else None
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
        # ── checkpoint (key present = created by a version that tracks it) ──
        "checkpointed_at": None,
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
    return _read_session_file(path)


def _read_session_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_own_session(project_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The session this location writes to — no global fallback, so a project
    never inherits (and overwrites) the global or another context's session."""
    return _read_session_file(get_session_path(project_root))


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
    *,
    mark_checkpoint: bool = True,
    fresh: bool = False,
) -> Dict[str, Any]:
    """
    Merge updates into the current session and save.
    Creates a new minimal session if none exists, or always when `fresh`
    (a new task: nothing is carried over from the previous session).
    Also auto-refreshes git branch + uncommitted files, and dual-writes
    to SQLite so every checkpoint is browsable (not just completed sessions).

    The checkpoint_* fields are stamped only when `updates` carries content, so
    hook calls like `checkpoint --quiet` don't make an old checkpoint look fresh.
    """
    goal = updates.get("goal")
    own = None if fresh else _load_own_session(project_root)
    # Sessions from before checkpoint tracking are often copies of another
    # project's session (shared id, unrelated content): start fresh, don't merge.
    existing = own if own and "checkpointed_at" in own else new_session(goal=goal)
    # Auto-refresh git state on every checkpoint
    head = _get_commit_sha()
    branch = _get_git_branch() or existing.get("branch")
    git_updates: Dict[str, Any] = {
        "branch":            branch,
        "commit_sha":        head or existing.get("commit_sha"),
        "ticket":            _extract_ticket(branch) or existing.get("ticket"),
        "uncommitted_files": _get_uncommitted_files() or existing.get("uncommitted_files", []),
    }
    # Auto-enrich goal from branch if not already set by user/AI
    if not existing.get("goal") and not updates.get("goal"):
        auto_goal = _branch_to_goal(branch)
        if auto_goal:
            git_updates["goal"] = auto_goal
    merged = {**existing, **git_updates, **updates}
    if mark_checkpoint and any(v not in (None, "", [], {}) for v in updates.values()):
        merged["checkpointed_at"] = _now()
        merged["checkpoint_commit_sha"] = head
        merged["checkpoint_branch"] = branch
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
    session = _load_own_session(project_root) or new_session()
    session["status"] = "complete"
    session["outcome"] = outcome
    if summary:
        session["summary"] = summary
    session["checkpointed_at"] = _now()
    session["checkpoint_commit_sha"] = _get_commit_sha()
    session["checkpoint_branch"] = _get_git_branch()
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


def checkpoint_is_stale(session: Optional[Dict[str, Any]] = None) -> bool:
    """
    True when there's no real checkpoint yet or the session was completed, i.e.
    the next task should start a new session. Commits don't count: agents commit
    mid-task, and inject already labels checkpoints with the commits since.
    """
    if session is None:
        session = _load_own_session()
    return not session or not session.get("checkpointed_at") or session.get("status") == "complete"


def _relative_age(iso_ts: Optional[str]) -> Optional[str]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - ts).total_seconds()
    for unit, size in (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400),
                       ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = int(secs // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def _format_last_checkpoint(session: Dict[str, Any]) -> Optional[str]:
    """Render the LAST CHECKPOINT block, labelled with its age and commits since."""
    if "checkpointed_at" in session and not session["checkpointed_at"]:
        return None  # never checkpointed (e.g. created by a hook, goal only from branch name)
    goal = (session.get("goal") or "").strip()
    hypothesis = (session.get("hypothesis") or "").strip()
    decisions = [str(d) for d in session.get("decisions_made") or [] if d]
    next_steps = [str(n) for n in session.get("next_steps") or [] if n]
    blockers = [str(b) for b in session.get("blockers") or [] if b]
    files = []
    for f in session.get("files_changed") or []:
        name = f.get("file") if isinstance(f, dict) else f
        if name:
            files.append(str(name))
    summary = (session.get("summary") or "").strip() if session.get("status") == "complete" else ""
    if not any((goal, hypothesis, decisions, next_steps, blockers, files, summary)):
        return None

    # branch/commit_sha/last_active are refreshed by every hook call, so only the
    # checkpoint_* fields say anything about when this content was saved.
    label: List[str] = []
    cp_branch = sha = None
    if not session.get("checkpointed_at"):
        label.append("age unknown — saved by an older agora-code")
    else:
        cp_branch = session.get("checkpoint_branch")
        sha = session.get("checkpoint_commit_sha")
        age = _relative_age(session["checkpointed_at"])
        if age:
            label.append(age)
        since = _commits_since(sha)
        if since:
            label.append(f"{since} commit{'s' if since != 1 else ''} since — may be outdated")
        elif since is None and sha:
            if cp_branch and cp_branch != _get_git_branch():
                label.append(f"saved on branch {cp_branch}")
            else:
                label.append("its commit is no longer in this branch's history")

    lines = ["LAST CHECKPOINT (" + ", ".join(label) + ")"]
    if goal:
        lines.append(f"  goal:       {goal[:120]}")
    if hypothesis:
        lines.append(f"  hypothesis: {hypothesis[:120]}")
    if decisions:
        lines.append("  decisions:  " + "; ".join(d[:80] for d in decisions[:3]))
    if next_steps:
        lines.append("  next:       " + "; ".join(n[:80] for n in next_steps[:3]))
    if blockers:
        lines.append("  blockers:   " + "; ".join(b[:80] for b in blockers[:2]))
    if files:
        lines.append("  files:      " + ", ".join(files[:6]))
    if summary:
        lines.append(f"  completed:  {summary[:120]}")
    if cp_branch or sha:
        lines.append(f"  branch:     {cp_branch or '?'}  commit: {(sha or '?')[:7]}")
    return "\n".join(lines)


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
    """Create a readable session ID like '2026-03-08-fix-post-users-3f9a1c'."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source = goal or branch
    if not source:
        return f"{date}-{uuid.uuid4().hex[:6]}"
    words = "".join(c if c.isalnum() else "-" for c in source.lower()).strip("-")
    words = "-".join(w for w in words.split("-") if w)[:40]
    # Suffix keeps ids unique: sessions table upserts by id across all projects
    return f"{date}-{words}-{uuid.uuid4().hex[:6]}"


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
      1. Last checkpoint  — from the latest session row, labelled with age + commits since
      2. Recent learnings — type-tagged, non-checkpoint findings
      3. Git log          — last 5 commits (always fresh)
      4. Uncommitted files
      5. Symbol index     — one-liners for recently touched files
    """
    import subprocess as _sp

    try:
        from agora_code.vector_store import get_store
        store = get_store()
        pid = project_id or _get_project_id()
        branch = _get_git_branch()
        parts: List[str] = []

        # ── 1. Last checkpoint — newest session row for this project with content ──
        # (hook-created sessions without content can be newer than the real checkpoint)
        rows = store._conn_().execute("""
            SELECT session_data FROM sessions
            WHERE project_id = ?
            ORDER BY last_active DESC LIMIT 20
        """, (pid,)).fetchall() if pid else []
        for (session_data,) in rows:
            try:
                checkpoint_block = _format_last_checkpoint(json.loads(session_data))
            except Exception:
                continue
            if checkpoint_block:
                parts.append(checkpoint_block)
                break

        # ── 2. Git log (live) — get recent commit SHAs for learning lookup ───
        def _git(cmd):
            try:
                r = _sp.run(cmd, capture_output=True, text=True, timeout=5)
                return r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                return ""

        # Last 3 commits on current branch + last 1 on main/master for cross-branch context
        branch_log = _git(["git", "log", "--format=%h|%s", "-3"])
        main_log = _git(["git", "log", "main", "--format=%h|%s", "-1"]) or \
                   _git(["git", "log", "master", "--format=%h|%s", "-1"])

        branch_shas = [l.split("|")[0] for l in branch_log.splitlines() if "|" in l]
        main_sha = main_log.split("|")[0] if "|" in main_log else ""
        all_shas = list(dict.fromkeys(branch_shas + ([main_sha] if main_sha else [])))

        git_log = _git(["git", "log", "--oneline", "-6"])

        # ── 3. Commit-based learnings (commit-relevant, not just recency) ────
        learnings_k = int(os.environ.get("AGORA_INJECT_LEARNINGS_K", "6"))
        TYPE_ICON = {"decision": "→", "blocker": "!", "next_step": "»", "finding": "·"}

        commit_learnings = store.get_learnings_for_commits(
            all_shas, project_id=pid, limit=learnings_k
        ) if all_shas else []

        # Fill remaining budget with recent non-checkpoint learnings not already included
        already_ids = {l["id"] for l in commit_learnings}
        remaining = learnings_k - len(commit_learnings)
        fallback_learnings = []
        if remaining > 0:
            rows = store._conn_().execute("""
                SELECT id, finding, type, confidence FROM learnings
                WHERE project_id = ?
                  AND (tags NOT LIKE '%checkpoint%' OR tags IS NULL)
                  AND (tags NOT LIKE '%conversation-summary%')
                  AND (tags NOT LIKE '%tool-failure%')
                ORDER BY timestamp DESC LIMIT ?
            """, (pid, remaining * 2)).fetchall()
            for row in rows:
                if row[0] not in already_ids:
                    fallback_learnings.append({"id": row[0], "finding": row[1],
                                               "type": row[2], "confidence": row[3]})
                if len(fallback_learnings) >= remaining:
                    break

        all_learnings = commit_learnings + fallback_learnings
        if all_learnings:
            section = ["LEARNINGS"]
            for lr in all_learnings:
                icon = TYPE_ICON.get(lr.get("type") or "finding", "·")
                conf = {"confirmed": "", "likely": "~", "hypothesis": "?"}.get(
                    lr.get("confidence") or "confirmed", ""
                )
                sha_tag = f" [{lr['commit_sha'][:7]}]" if lr.get("commit_sha") else ""
                ev = (lr.get('evidence') or '').strip()[:200]
                _files = lr.get('files') or []
                if isinstance(_files, str):
                    import json as _j
                    try: _files = _j.loads(_files)
                    except Exception: _files = []
                file_tag = f"  [{_files[0].split('/')[-1]}]" if _files else ""
                line = ev if ev else (lr.get('finding') or '')[:120]
                section.append(f"  {icon}{conf}{sha_tag} {line}{file_tag}")
            parts.append("\n".join(section))

            # Mark these learnings as injected
            try:
                store.mark_learnings_injected([l["id"] for l in all_learnings if l.get("id")])
            except Exception:
                pass

        # ── 4. Uncommitted work — change notes + dirty files ─────────────────
        uncommitted = _git(["git", "diff", "--name-only", "HEAD"]).splitlines()
        staged = _git(["git", "diff", "--cached", "--name-only"]).splitlines()
        dirty = list(dict.fromkeys(uncommitted + staged))[:8]

        if dirty:
            # Check if we have LLM change notes for these files
            uncommitted_notes = store.get_uncommitted_file_changes(
                project_id=pid, branch=branch, limit=20
            )
            noted_files = {n["file_path"]: n["diff_summary"] for n in uncommitted_notes}
            # Also match by basename for robustness
            noted_base = {n["file_path"].split("/")[-1]: n["diff_summary"] for n in uncommitted_notes}

            has_notes = any(
                fp in noted_files or fp.split("/")[-1] in noted_base
                for fp in dirty
            )

            if has_notes:
                section = ["UNCOMMITTED WORK"]
                for fp in dirty[:6]:
                    note = noted_files.get(fp) or noted_base.get(fp.split("/")[-1], "")
                    if note:
                        # Strip leading "filepath: " prefix that _summarize_diff adds
                        # Note may use absolute path, relative path, or basename
                        clean_note = note
                        import re as _re
                        clean_note = _re.sub(r'^[^\s:]+[/\\][^\s:]*:\s*', '', clean_note, count=1)
                        section.append(f"  {fp}  {clean_note[:100]}")
                    else:
                        section.append(f"  {fp}  (no change note yet)")
                parts.append("\n".join(section))
            else:
                parts.append("UNCOMMITTED\n  " + ", ".join(dirty))

        # ── 4b. Git log — after learnings/uncommitted ────────────────────────
        if git_log:
            parts.append("GIT LOG\n" + "\n".join(f"  {l}" for l in git_log.splitlines()))

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

#!/bin/sh
# on-stop.sh — fires when Claude finishes a response.
#
# 1. Reads JSONL transcript (via transcript_path or auto-discovery)
# 2. Gradient-compresses: last 4 exchanges full, middle = topic labels, first quarter skipped
# 3. Extracts structured checkpoint: goal, decisions, next_steps, blockers, files
# 4. Stores ONE structured learning per session (replaces previous checkpoint)
# 5. Updates session.json
#
# Hook input JSON (stdin): {"session_id":"...","transcript_path":"...","prompt":"..."}

INPUT=$(cat)

# Write input to temp file — avoids quoting issues with env vars and heredocs
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

# Always save a basic checkpoint first
agora-code checkpoint --quiet 2>/dev/null || true

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os, re, subprocess, shutil
from pathlib import Path

tmpfile = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    with open(tmpfile) as f:
        hook = json.load(f)
except Exception:
    sys.exit(0)
finally:
    try:
        os.unlink(tmpfile)
    except Exception:
        pass

session_id = hook.get("session_id", "")

# ── Find transcript ───────────────────────────────────────────────────────────
transcript_path = hook.get("transcript_path", "")

if not transcript_path or not os.path.isfile(transcript_path):
    # Auto-discover: find most recently modified JSONL in the project's claude dir
    try:
        import subprocess as sp
        result = sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        project_root = result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        project_root = ""

    home = str(Path.home())
    # ~/.claude/projects/<encoded-path>/
    if project_root:
        encoded = project_root.replace("/", "-").lstrip("-")
        project_dir = Path(home) / ".claude" / "projects" / encoded
    else:
        project_dir = None

    if project_dir and project_dir.exists():
        jsonl_files = sorted(
            project_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if jsonl_files:
            transcript_path = str(jsonl_files[0])

if not transcript_path or not os.path.isfile(transcript_path):
    sys.exit(0)

# ── Parse transcript ──────────────────────────────────────────────────────────
messages = []
try:
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                role = d.get("message", {}).get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content = d.get("message", {}).get("content", "")
                text = ""
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            text += c.get("text", "")
                elif isinstance(content, str):
                    text = content
                text = text.strip()
                if text and len(text) > 10:
                    messages.append((role, text))
            except Exception:
                continue
except Exception:
    sys.exit(0)

if len(messages) < 2:
    sys.exit(0)

# ── Gradient compression ──────────────────────────────────────────────────────
# Last 8 messages (4 exchanges): keep up to 500 chars each
# Middle: first line only (80 chars, topic label)
# First quarter: skip entirely — least relevant to current work

total = len(messages)
recent_cutoff = max(0, total - 8)
skip_cutoff   = total // 4

compressed = []
for i, (role, text) in enumerate(messages):
    if i < skip_cutoff:
        continue
    if i >= recent_cutoff:
        compressed.append((role, text[:500]))
    else:
        compressed.append((role, text.split("\n")[0].strip()[:80]))

# ── Extract structured fields ─────────────────────────────────────────────────
DECISION_RE = re.compile(
    r'\b(decided?|using|switched? to|changed? to|moved? to|chose?|set to|now using|replaced? with|migrated? to)\b',
    re.I
)
BLOCKER_RE = re.compile(
    r'\b(blocked?|error|fail(?:ed|ing)?|can\'t|cannot|broken|issue|denied|permission|not working)\b',
    re.I
)
NEXT_RE = re.compile(
    r'\b(next step|todo|still need to|should now|want to|ready to|step [2-9]:|will need to)\b',
    re.I
)
# Skip lines that are clearly me (the AI) describing actions I just took
SELF_ACTION_RE = re.compile(
    r'^(now i|let me|i\'ll|i need to|let\'s|reading|writing|checking|adding|updating|fixing)',
    re.I
)
FILLER_RE = re.compile(
    r'^(hi|hey|hello|ok|okay|yes|no|sure|thanks|bye|lol|cool|great|nice|yep|nope|got it|sounds good)\b',
    re.I
)
FILE_RE = re.compile(r'[\w./\-]+\.(?:py|sh|json|ts|js|md|toml|yaml|yml)\b')

def is_substantive(t):
    t = t.strip()
    return len(t) > 30 and not FILLER_RE.match(t)

# Goal = last substantive user message (first line)
goal = ""
for role, text in reversed(compressed):
    if role == "user" and is_substantive(text):
        goal = text.split("\n")[0].strip()[:200]
        break

decisions, next_steps, blockers = [], [], []
files_mentioned = set()

for role, text in compressed:
    for fp in FILE_RE.findall(text):
        if "/" in fp or fp.startswith("."):
            files_mentioned.add(fp)

    if role != "assistant":
        continue

    for line in text.split("\n"):
        line = line.strip()
        if len(line) < 15 or len(line) > 220:
            continue
        clean = re.sub(r'^[-*•`#]+\s*', '', line).strip()
        if not clean:
            continue
        if SELF_ACTION_RE.match(clean):
            continue
        if DECISION_RE.search(line) and clean not in decisions:
            decisions.append(clean)
        if BLOCKER_RE.search(line) and clean not in blockers:
            blockers.append(clean)
        if NEXT_RE.search(line) and clean not in next_steps:
            next_steps.append(clean)

decisions  = decisions[:5]
next_steps = next_steps[:5]
blockers   = blockers[:3]

# ── Git context ───────────────────────────────────────────────────────────────
def git(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""

branch     = git(["git", "branch", "--show-current"])
commit_sha = git(["git", "rev-parse", "--short", "HEAD"])
unstaged   = git(["git", "diff", "--name-only", "HEAD"]).splitlines()
staged     = git(["git", "diff", "--cached", "--name-only"]).splitlines()
files_touched = list({*unstaged, *staged, *files_mentioned})[:20]

# ── Build checkpoint JSON ─────────────────────────────────────────────────────
transcript_json = json.dumps(
    [{"role": r, "text": t} for r, t in compressed],
    ensure_ascii=False
)

checkpoint = {
    "goal":          goal,
    "decisions":     decisions,
    "next_steps":    next_steps,
    "blockers":      blockers,
    "files_touched": files_touched,
    "branch":        branch,
    "commit_sha":    commit_sha,
    "session_id":    session_id,
    "transcript":    json.loads(transcript_json),
}

# ── Store structured learning + save transcript to session ────────────────────
agora_bin = shutil.which("agora-code") or "agora-code"

try:
    import sqlite3
    db_path = os.path.expanduser(
        os.environ.get("AGORA_CODE_DB", "~/.agora-code/memory.db")
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM learnings WHERE session_id=? AND tags LIKE '%checkpoint%'",
        (session_id,)
    )
    # Store compressed transcript in session_data
    existing = conn.execute(
        "SELECT session_data FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if existing:
        try:
            sd = json.loads(existing[0] or "{}")
        except Exception:
            sd = {}
        sd["compressed_transcript"] = json.loads(transcript_json)
        sd["branch"] = branch
        sd["commit_sha"] = commit_sha
        conn.execute(
            "UPDATE sessions SET session_data=?, branch=?, commit_sha=?, last_active=datetime('now') WHERE session_id=?",
            (json.dumps(sd), branch, commit_sha, session_id)
        )
    conn.commit()
    conn.close()
except Exception:
    pass

finding = (
    f"CHECKPOINT | goal: {goal[:100]}"
    + (f" | decisions: {'; '.join(d[:60] for d in decisions[:2])}" if decisions else "")
    + (f" | next: {'; '.join(n[:60] for n in next_steps[:2])}" if next_steps else "")
)
evidence = json.dumps(checkpoint, ensure_ascii=False)[:1000]

_log = os.path.expanduser("~/.agora-code/hooks.log")
result = subprocess.run(
    [agora_bin, "learn", finding,
     "--evidence", evidence,
     "--confidence", "confirmed",
     "--tags", "checkpoint,structured"],
    capture_output=True, timeout=10,
)
if result.returncode != 0:
    try:
        with open(_log, "a") as _f:
            _f.write(f"[on-stop] learn failed (rc={result.returncode}): {result.stderr.decode()[:200]}\n")
    except Exception:
        pass

# Also update session.json
try:
    args = ["checkpoint", "--quiet"]
    if goal:
        args += ["--goal", goal[:200]]
    for ns in next_steps[:3]:
        args += ["--next-step", ns[:100]]
    for b in blockers[:2]:
        args += ["--blocker", b[:100]]
    for fp in files_touched[:5]:
        args += ["--file-changed", fp]
    subprocess.run([agora_bin] + args, capture_output=True, timeout=10)
except Exception:
    pass

PYEOF

exit 0

#!/bin/sh
# on-bash.sh — PostToolUse(Bash): detect git commits, tag file_changes + symbol_notes.
# Input JSON: {"tool_input":{"command":"..."},"tool_response":{"stdout":"..."}}

INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os, re, subprocess

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f: hook = json.load(_f)
tool_input = hook.get("tool_input") or {}
command = tool_input.get("command", "")

# Only care about git commit commands
if "git commit" not in command:
    sys.exit(0)

# Check stdout for actual commit sha
stdout = (hook.get("tool_response") or {}).get("stdout", "")

# Try to get commit sha from git directly (most reliable)
try:
    r = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=5
    )
    commit_sha = r.stdout.strip() if r.returncode == 0 else ""
except Exception:
    commit_sha = ""

if not commit_sha:
    # Parse from stdout: "[main abc1234] ..."
    m = re.search(r'\[[\w/\-]+ ([0-9a-f]{5,12})\]', stdout)
    commit_sha = m.group(1) if m else ""

if not commit_sha:
    sys.exit(0)

# Get files in this commit
try:
    r = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", commit_sha],
        capture_output=True, text=True, timeout=5
    )
    committed_files = r.stdout.strip().splitlines() if r.returncode == 0 else []
except Exception:
    committed_files = []

if not committed_files:
    # Fallback: files staged before commit
    try:
        r = subprocess.run(
            ["git", "show", "--name-only", "--format=", commit_sha],
            capture_output=True, text=True, timeout=5
        )
        committed_files = [l for l in r.stdout.strip().splitlines() if l.strip()]
    except Exception:
        committed_files = []

if not committed_files:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import tag_commit

    tag_commit(
        commit_sha,
        committed_files,
        project_id=_get_project_id(),
        branch=_get_git_branch(),
    )
except Exception:
    pass
PYEOF

exit 0

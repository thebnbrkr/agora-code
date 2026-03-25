#!/bin/sh
# on-read.sh — PostToolUse(Read): index file symbols on first read.
# Input JSON: {"tool_input":{"file_path":"...","offset":N,"limit":N},"tool_response":...}

INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os
from pathlib import Path

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f: hook = json.load(_f)
tool_input = hook.get("tool_input") or {}
file_path = tool_input.get("file_path", "")

if not file_path or not Path(file_path).exists():
    sys.exit(0)

# Only index code files — skip binary, large files, non-code
ext = Path(file_path).suffix.lower()
CODE_EXTS = {".py",".js",".ts",".jsx",".tsx",".go",".rs",".java",
             ".c",".cpp",".cs",".rb",".swift",".kt",".php",".sh"}
if ext not in CODE_EXTS:
    sys.exit(0)

# Skip large files (> 500KB) — let summarize handle those
try:
    size = Path(file_path).stat().st_size
    if size > 500_000:
        sys.exit(0)
except Exception:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    from agora_code.vector_store import get_store
    from agora_code.indexer import index_file

    store = get_store()
    project_id = _get_project_id()
    branch = _get_git_branch()
    commit_sha = _get_commit_sha()

    # Only index if not already indexed at this commit
    existing = store.get_file_snapshot(file_path, project_id=project_id, branch=branch)
    if existing and existing.get("commit_sha") == commit_sha:
        sys.exit(0)  # already up to date

    index_file(file_path, project_id=project_id, branch=branch, commit_sha=commit_sha)
except Exception:
    pass
PYEOF

exit 0

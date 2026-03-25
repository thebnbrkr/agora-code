#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
# on-edit.sh — PostToolUse(Write|Edit|MultiEdit): re-index symbols + track diff.

INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

# Track diff (existing behaviour)
python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os, subprocess, shutil
from pathlib import Path
try:
    with open(sys.argv[1]) as f:
        hook = json.load(f)
except Exception:
    sys.exit(0)
fp = (hook.get("tool_input") or {}).get("file_path", "")
agora = shutil.which("agora-code") or "agora-code"
if fp:
    subprocess.run([agora, "track-diff", fp], capture_output=True, timeout=10)
PYEOF

# Re-index symbols
python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os
from pathlib import Path
try:
    with open(sys.argv[1]) as f:
        hook = json.load(f)
except Exception:
    sys.exit(0)
finally:
    try: os.unlink(sys.argv[1])
    except: pass

fp = (hook.get("tool_input") or {}).get("file_path", "")
if not fp or not Path(fp).exists():
    sys.exit(0)

ext = Path(fp).suffix.lower()
CODE_EXTS = {".py",".js",".ts",".jsx",".tsx",".go",".rs",".java",
             ".c",".cpp",".cs",".rb",".swift",".kt",".php",".sh"}
if ext not in CODE_EXTS:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    from agora_code.indexer import index_file
    index_file(fp, project_id=_get_project_id(), branch=_get_git_branch(), commit_sha=_get_commit_sha())
except Exception:
    pass
PYEOF

exit 0

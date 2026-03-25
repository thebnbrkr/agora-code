#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os
from pathlib import Path

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f:
    try:
        hook = json.load(_f)
    except Exception:
        sys.exit(0)

response = str(hook.get('tool_response', ''))
CODE_EXTS = {'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php','.sh'}
seen = set()
for line in response.splitlines():
    candidate = line.split(':')[0].strip()
    if candidate and candidate not in seen and os.path.isfile(candidate):
        if Path(candidate).suffix.lower() in CODE_EXTS:
            seen.add(candidate)

if not seen:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    project_id = _get_project_id()
    branch = _get_git_branch()
    commit_sha = _get_commit_sha()
except Exception:
    project_id = branch = commit_sha = None

try:
    from agora_code.indexer import index_file
    for fp in seen:
        index_file(fp, project_id=project_id, branch=branch, commit_sha=commit_sha)
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0

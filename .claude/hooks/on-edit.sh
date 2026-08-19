#!/bin/sh
INPUT=$(cat)
TMPFILE=$(mktemp /tmp/agora_hook_XXXXXX)
printf '%s' "$INPUT" > "$TMPFILE"

python3 - "$TMPFILE" << 'PYEOF'
import sys, json, os, subprocess

with open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null") as _f:
    try:
        hook = json.load(_f)
    except Exception:
        sys.exit(0)

file_path = (hook.get('tool_input') or {}).get('file_path', '')
if not file_path or not os.path.isfile(file_path):
    sys.exit(0)

CODE_EXTS = {'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php'}
if not any(file_path.endswith(e) for e in CODE_EXTS):
    sys.exit(0)

try:
    subprocess.run(['agora-code', 'track-diff', file_path], capture_output=True, timeout=10)
except Exception:
    pass

try:
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import index_file
    index_file(file_path, project_id=_get_project_id(), branch=_get_git_branch())
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0

#!/bin/sh
INPUT=$(cat)
python3 - << 'PYEOF'
import sys, json, os, subprocess

try:
    import select
    data = sys.stdin.read() if select.select([sys.stdin], [], [], 0)[0] else ''
except Exception:
    data = ''

try:
    d = json.loads(data) if data else {}
except Exception:
    d = {}

file_path = (d.get('tool_input') or {}).get('file_path', '')
if not file_path or not os.path.isfile(file_path):
    sys.exit(0)

CODE_EXTS = {'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php'}
if not any(file_path.endswith(e) for e in CODE_EXTS):
    sys.exit(0)

# Track the diff
agora_bin = 'agora-code'
try:
    subprocess.run([agora_bin, 'track-diff', file_path], capture_output=True, timeout=10)
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
exit 0

#!/bin/sh
# on-grep.sh — PostToolUse(Grep): index files matched by grep results.
# Input JSON: {"tool_input":{"pattern":"...","path":"..."},"tool_response":"..."}

INPUT=$(cat)
python3 - << 'PYEOF'
import sys, json, os
from pathlib import Path

try:
    import select
    data = sys.stdin.read() if select.select([sys.stdin], [], [], 0)[0] else ''
except Exception:
    data = ''

try:
    d = json.loads(data) if data else {}
except Exception:
    d = {}

response = str(d.get('tool_response', ''))
CODE_EXTS = {'.py','.js','.ts','.jsx','.tsx','.go','.rs','.java','.c','.cpp','.cs','.rb','.swift','.kt','.php','.sh'}
seen = set()
for line in response.splitlines():
    # files_with_matches mode: just a path; content mode: path:linenum:text
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
exit 0

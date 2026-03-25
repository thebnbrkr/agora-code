#!/bin/sh
INPUT=$(cat)
python3 - << 'PYEOF'
import sys, json, os

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

try:
    from agora_code.session import _get_project_id, _get_git_branch, _get_commit_sha
    project_id = _get_project_id()
    branch = _get_git_branch()
    commit_sha = _get_commit_sha()
except Exception:
    project_id = branch = commit_sha = None

# Skip if already indexed at this commit
if commit_sha:
    try:
        import sqlite3
        db_path = os.path.expanduser(os.environ.get('AGORA_CODE_DB', '~/.agora-code/memory.db'))
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            'SELECT 1 FROM symbol_notes WHERE file_path=? AND commit_sha=? LIMIT 1',
            (file_path, commit_sha)
        ).fetchone()
        conn.close()
        if row:
            sys.exit(0)
    except Exception:
        pass

try:
    from agora_code.indexer import index_file
    index_file(file_path, project_id=project_id, branch=branch, commit_sha=commit_sha)
except Exception:
    pass

sys.exit(0)
PYEOF
exit 0

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

command = (d.get('tool_input') or {}).get('command', '')
if 'git' not in command or 'commit' not in command:
    sys.exit(0)

try:
    result = subprocess.run(['git','rev-parse','--short','HEAD'], capture_output=True, text=True, timeout=5)
    commit_sha = result.stdout.strip() if result.returncode == 0 else ''
    if not commit_sha:
        sys.exit(0)
    result = subprocess.run(
        ['git','diff-tree','--no-commit-id','-r','--name-only', commit_sha],
        capture_output=True, text=True, timeout=5
    )
    files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
except Exception:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import tag_commit
    tag_commit(commit_sha, files, project_id=_get_project_id(), branch=_get_git_branch())
except Exception:
    pass

sys.exit(0)
PYEOF
exit 0

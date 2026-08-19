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

command = (hook.get('tool_input') or {}).get('command', '')
if 'git' not in command or 'commit' not in command:
    sys.exit(0)

try:
    r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, timeout=5)
    commit_sha = r.stdout.strip() if r.returncode == 0 else ''
    if not commit_sha:
        sys.exit(0)
    r = subprocess.run(
        ['git', 'diff-tree', '--no-commit-id', '-r', '--name-only', commit_sha],
        capture_output=True, text=True, timeout=5
    )
    files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
except Exception:
    sys.exit(0)

try:
    from agora_code.session import _get_project_id, _get_git_branch
    from agora_code.indexer import tag_commit
    tag_commit(commit_sha, files, project_id=_get_project_id(), branch=_get_git_branch())
except Exception:
    pass

try:
    subprocess.run(['agora-code', 'learn-from-commit', commit_sha, '--quiet'],
                   timeout=30, capture_output=True)
except Exception:
    pass

sys.exit(0)
PYEOF

rm -f "$TMPFILE"
exit 0

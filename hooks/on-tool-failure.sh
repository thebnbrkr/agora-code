#!/bin/sh
# Claude Code PostToolUseFailure hook — track tool failures in session memory.
#
# Fires after a tool call fails. Stores the error so it can be recalled
# if the same failure recurs.
#
# Input JSON: {"tool_name":"...","tool_input":{...},"error":"...",...}
# Output: stdout with any advice. Exit 0 = continue.

INPUT=$(cat)

ERROR_INFO=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    tool = d.get('tool_name', 'unknown')
    err = d.get('error', '') or ''
    ti = d.get('tool_input', {})
    if isinstance(ti, str):
        import json as j2
        ti = j2.loads(ti)
    path = ti.get('file_path') or ti.get('path') or ti.get('command') or ''
    if err:
        print(f'{tool} failed on {path}: {err[:200]}')
    else:
        print('')
except Exception:
    print('')
" 2>/dev/null)

if [ -n "$ERROR_INFO" ]; then
    agora-code learn "$ERROR_INFO" --confidence hypothesis --tags tool-failure 2>/dev/null || true
fi

exit 0

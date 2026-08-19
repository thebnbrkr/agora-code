#!/bin/sh
# Cursor postToolUse hook — track tool calls and inject session context.
# Fires after every successful tool execution.
# For file edits: runs track-diff. For all tools: can inject additional_context.

INPUT=$(cat)

TOOL_NAME=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('tool_name', ''))
except Exception:
    print('')
" 2>/dev/null)

case "$TOOL_NAME" in
    Write|Edit|MultiEdit)
        FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    ti = d.get('tool_input', {})
    if isinstance(ti, str):
        import json as j2
        ti = j2.loads(ti)
    print(ti.get('file_path') or ti.get('path') or '')
except Exception:
    print('')
" 2>/dev/null)
        if [ -n "$FILE_PATH" ]; then
            agora-code track-diff "$FILE_PATH" 2>/dev/null || true
        fi
        ;;
esac

printf '{}\n'

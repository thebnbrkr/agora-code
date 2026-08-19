#!/bin/sh
# Gemini CLI AfterTool hook — track file changes after write/edit tools.
#
# Input JSON via stdin includes tool_name, tool_input, tool_response.
# Output JSON to stdout. Can inject additionalContext via hookSpecificOutput.

INPUT=$(cat)

FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    ti = d.get('tool_input', {})
    if isinstance(ti, str):
        import json as j2
        ti = j2.loads(ti)
    print(ti.get('file_path') or ti.get('path') or ti.get('target_file') or '')
except Exception:
    print('')
" 2>/dev/null)

if [ -n "$FILE_PATH" ]; then
    agora-code track-diff "$FILE_PATH" 2>/dev/null || true
fi

printf '{}\n'

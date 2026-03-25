#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
INPUT=$(cat)
FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('file_path') or d.get('path') or '')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then exit 0; fi

RESULT=$(agora-code summarize "$FILE_PATH" --json-output 2>/dev/null)
if [ -z "$RESULT" ]; then exit 0; fi

ACTION=$(printf '%s' "$RESULT" | python3 -c "
import sys, json
try:
    print(json.loads(sys.stdin.read()).get('action', 'allow'))
except Exception:
    print('allow')
" 2>/dev/null)

if [ "$ACTION" = "summarize" ]; then
    printf '%s' "$RESULT" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(d.get('summary', ''))
print()
print(f'[Read blocked: file has {d.get(\"original_lines\", 0)} lines. Use the summary above — do NOT read this file in chunks.]')
" 2>/dev/null
    exit 2
fi
exit 0

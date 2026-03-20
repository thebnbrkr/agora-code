#!/bin/sh
# Claude Code PreToolUse:Read hook — intercept large file reads.
# Claude Code passes the file path via $CLAUDE_TOOL_INPUT_FILE_PATH env var
# and reads JSON from stdin. Output: allow (exit 0) or block (exit 2).
#
# For large files: exit 2 + print summary to stdout → Claude gets the summary
# instead of the raw file content.

INPUT=$(cat)

FILE_PATH="${CLAUDE_TOOL_INPUT_FILE_PATH}"

if [ -z "$FILE_PATH" ]; then
    FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('file_path') or d.get('path') or '')
except Exception:
    print('')
" 2>/dev/null)
fi

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

RESULT=$(agora-code summarize "$FILE_PATH" --json-output 2>/tmp/agora-pre-read-error.log)

if [ -z "$RESULT" ]; then
    # Log silent failures so they're diagnosable (was 2>/dev/null before)
    exit 0
fi

ACTION=$(printf '%s' "$RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('action', 'allow'))
except Exception:
    print('allow')
" 2>>/tmp/agora-pre-read-error.log)

if [ "$ACTION" = "summarize" ]; then
    printf '%s' "$RESULT" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
s = d.get('summary', '')
orig = d.get('original_lines', 0)
toks = d.get('summary_tokens', 0)
print(s)
print()
print(f'[Read blocked: file has {orig} lines. Use the summary above — do NOT read this file in chunks.]')
" 2>>/tmp/agora-pre-read-error.log
    exit 2
else
    exit 0
fi

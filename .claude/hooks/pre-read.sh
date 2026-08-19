#!/bin/sh
# PreToolUse(Read) — intercept large-file reads, serve AST summary instead.
# Claude Code nests tool args under "tool_input"; fall back to top-level
# keys for harnesses that send a flat shape.
INPUT=$(cat)
PARSED=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    ti = d.get('tool_input') or {}
    if isinstance(ti, str):
        ti = json.loads(ti)
    path = ti.get('file_path') or ti.get('path') or d.get('file_path') or d.get('path') or ''
    # Targeted read (offset/limit present) — the agent already knows where to
    # look, likely from a prior summary. Let it through untouched.
    targeted = ti.get('offset') is not None or ti.get('limit') is not None
    print(json.dumps({'path': path, 'targeted': targeted}))
except Exception:
    print(json.dumps({'path': '', 'targeted': False}))
" 2>/dev/null)

FILE_PATH=$(printf '%s' "$PARSED" | python3 -c "import sys,json;print(json.loads(sys.stdin.read())['path'])" 2>/dev/null)
TARGETED=$(printf '%s' "$PARSED" | python3 -c "import sys,json;print('yes' if json.loads(sys.stdin.read())['targeted'] else 'no')" 2>/dev/null)

if [ -z "$FILE_PATH" ] || [ "$TARGETED" = "yes" ]; then exit 0; fi

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
n = d.get('original_lines', 0)
size = f'file has {n} lines' if n else 'large file'
print(f'[Read blocked: {size}. Use the summary above, or Read with offset+limit for specific sections.]')
" 2>/dev/null
    exit 2
fi
exit 0

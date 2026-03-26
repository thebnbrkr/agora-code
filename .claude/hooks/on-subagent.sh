#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
INPUT=$(cat)

# Block Explore subagent — it bypasses agora-code hooks (pre-read, on-read, etc.)
# All file exploration must go through Read/Grep/Glob in the main session.
IS_EXPLORE=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    t = str(d.get('subagent_type', d.get('type', d.get('agent_type', '')))).lower()
    print('yes' if 'explore' in t else 'no')
except Exception:
    print('no')
" 2>/dev/null)

if [ "$IS_EXPLORE" = "yes" ]; then
    printf 'agora-code: Explore subagent blocked. Use Read/Grep/Glob directly in the main session so hooks fire and files get indexed.\n'
    exit 1
fi

CONTEXT=$(agora-code inject --quiet --level summary 2>/dev/null)
if [ -n "$CONTEXT" ]; then
    printf '[agora-code: parent session context]\n%s\n' "$CONTEXT"
fi
exit 0

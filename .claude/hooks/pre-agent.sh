#!/bin/sh
# PreToolUse(Agent) — fires before any Agent tool call, can block (exit 2)
INPUT=$(cat)

SUBAGENT_TYPE=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('tool_input', {}).get('subagent_type', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ "$SUBAGENT_TYPE" = "Explore" ]; then
    printf 'agora-code: BLOCKED — Explore subagent bypasses hooks (pre-read, on-read, etc.).\n' >&2
    printf 'Use Read/Grep/Glob directly in the main session.\n' >&2
    printf 'Rule: run `agora-code summarize <file>` before reading any file >50 lines.\n' >&2
    exit 2
fi

exit 0

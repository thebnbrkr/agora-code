#!/bin/sh
# Claude Code UserPromptSubmit hook — auto-set goal + recall relevant learnings.
#
# Fires when the user submits a prompt.
# 1. If no session goal exists, sets the first prompt as the goal automatically.
# 2. Searches stored learnings for anything relevant and appends as context.
#
# Input JSON: {"prompt":"...","session_id":"...","transcript_path":"..."}
# Output: stdout with context to append. Exit 0 = allow, exit 2 = block.

INPUT=$(cat)

PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$PROMPT" ]; then
    exit 0
fi

# Auto-set goal from first prompt if no goal is set yet
CURRENT_GOAL=$(agora-code inject --quiet 2>/dev/null)
if [ -z "$CURRENT_GOAL" ]; then
    SHORT_GOAL=$(printf '%s' "$PROMPT" | cut -c1-120)
    agora-code checkpoint --goal "$SHORT_GOAL" --quiet 2>/dev/null || true
fi

# Recall relevant learnings for this prompt
LEARNINGS=$(agora-code recall "$PROMPT" --limit 2 2>/dev/null)

if [ -n "$LEARNINGS" ] && ! echo "$LEARNINGS" | grep -q "No learnings match"; then
    printf '[agora-code: relevant learnings for this prompt]\n%s\n' "$LEARNINGS"
fi

exit 0

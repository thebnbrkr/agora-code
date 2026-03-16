#!/bin/sh
# Claude Code UserPromptSubmit hook — auto-set goal + recall relevant learnings.
#
# Fires when the user submits a prompt.
# 1. If no session goal exists and this prompt is substantive, sets it as goal.
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

# Auto-set goal from prompt if no goal is set yet — only if substantive
CURRENT_GOAL=$(agora-code inject --quiet 2>/dev/null)
if [ -z "$CURRENT_GOAL" ]; then
    IS_SUBSTANTIVE=$(printf '%s' "$PROMPT" | python3 -c "
import sys, re
text = sys.stdin.read().strip()
# Skip: too short, greetings, single words, pure commands
if len(text) < 30:
    print('no')
elif re.match(r'^(hi|hey|hello|ok|okay|yes|no|sure|thanks|bye|lol)\b', text, re.I):
    print('no')
elif re.match(r'^agora-code\s', text):
    print('no')
else:
    print('yes')
" 2>/dev/null)
    if [ "$IS_SUBSTANTIVE" = "yes" ]; then
        SHORT_GOAL=$(printf '%s' "$PROMPT" | cut -c1-120)
        agora-code checkpoint --goal "$SHORT_GOAL" --quiet 2>/dev/null || true
    fi
fi

# Recall relevant learnings for this prompt
LEARNINGS=$(agora-code recall "$PROMPT" --limit 2 2>/dev/null)

if [ -n "$LEARNINGS" ] && ! echo "$LEARNINGS" | grep -q "No learnings match"; then
    printf '[agora-code: relevant learnings for this prompt]\n%s\n' "$LEARNINGS"
fi

exit 0

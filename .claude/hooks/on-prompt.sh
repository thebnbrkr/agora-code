#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
INPUT=$(cat)

PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$PROMPT" ]; then exit 0; fi

# Auto-set goal from first substantive prompt if no goal exists yet
CURRENT_GOAL=$(agora-code inject --quiet 2>/dev/null)
if [ -z "$CURRENT_GOAL" ]; then
    IS_SUBSTANTIVE=$(printf '%s' "$PROMPT" | python3 -c "
import sys, re
text = sys.stdin.read().strip()
if len(text) < 30:
    print('no')
elif re.match(r'^(hi|hey|hello|ok|okay|yes|no|sure|thanks|bye|lol)\\b', text, re.I):
    print('no')
elif re.match(r'^agora-code\\s', text):
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

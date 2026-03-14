#!/bin/sh
# Claude Code UserPromptSubmit hook — enrich user prompts with relevant learnings.
#
# Fires when the user submits a prompt. Searches stored learnings for
# anything relevant and appends it as context.
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

LEARNINGS=$(agora-code recall "$PROMPT" --limit 2 2>/dev/null | head -20)

if [ -n "$LEARNINGS" ] && ! echo "$LEARNINGS" | grep -q "No learnings match"; then
    printf '[agora-code: relevant learnings for this prompt]\n%s\n' "$LEARNINGS"
fi

exit 0

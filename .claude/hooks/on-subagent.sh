#!/bin/sh
# SubagentStart — inject agora-code session context into the subagent.
# Cannot block (SubagentStart is observation-only); blocking is handled by
# pre-agent.sh at PreToolUse(Agent). Context injection requires the
# hookSpecificOutput JSON format — plain stdout is ignored by SubagentStart.
CONTEXT=$(agora-code inject --quiet --level summary 2>/dev/null)
if [ -n "$CONTEXT" ]; then
    printf '%s' "$CONTEXT" | python3 -c "
import sys, json
context = sys.stdin.read()
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SubagentStart',
        'additionalContext': '[agora-code: parent session context]\n' + context
    }
}))
" 2>/dev/null
fi
exit 0

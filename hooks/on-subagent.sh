#!/bin/sh
# Claude Code SubagentStart hook — inject session context into subagents.
#
# Fires when a subagent spawns. Injects compressed session state so the
# subagent knows what the parent conversation is working on.
#
# Input JSON: {"session_id":"...","transcript_path":"...",...}
# Output: stdout with context. Exit 0 = proceed.

cat > /dev/null

CONTEXT=$(agora-code inject --quiet --level summary 2>/dev/null)

if [ -n "$CONTEXT" ]; then
    printf '[agora-code: parent session context]\n%s\n' "$CONTEXT"
fi

exit 0

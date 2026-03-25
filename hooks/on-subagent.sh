#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
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

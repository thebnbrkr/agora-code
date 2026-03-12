#!/bin/sh
# Cursor sessionStart hook — inject session context into the agent.
# Cursor sends JSON via stdin; we read and discard it, then run inject.
# Output MUST be JSON: {"additional_context": "..."} — plain text causes a parse error.
cat > /dev/null
context=$(agora-code inject --quiet 2>/dev/null)
if [ -n "$context" ]; then
    # Escape backslashes, double-quotes, and newlines for valid JSON string
    escaped=$(printf '%s' "$context" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
    printf '{"additional_context":%s}\n' "$escaped"
else
    printf '{}\n'
fi

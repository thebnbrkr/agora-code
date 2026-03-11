#!/bin/sh
# Cursor sessionStart hook — inject session context into the agent.
# Cursor sends JSON via stdin; we read and discard it, then run inject.
cat > /dev/null
agora-code inject --quiet
exit 0

#!/bin/sh
cat > /dev/null
CONTEXT=$(agora-code inject --quiet --level summary 2>/dev/null)
if [ -n "$CONTEXT" ]; then
    printf '[agora-code: parent session context]\n%s\n' "$CONTEXT"
fi
exit 0

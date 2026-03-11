#!/bin/sh
# Cursor preCompact hook — save session state before context window is compacted.
# Cursor sends JSON via stdin; we read and discard it, then checkpoint.
cat > /dev/null
agora-code checkpoint --quiet
exit 0

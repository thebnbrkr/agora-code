#!/bin/sh
# Cursor afterFileEdit hook — track the edited file and refresh route cache.
# Cursor sends JSON via stdin with a "filePath" field, e.g.:
#   {"filePath": "agora_code/auth.py", ...}
# We extract it with python3 (always available alongside agora-code).

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('filePath', data.get('file_path', data.get('path', ''))))
except Exception:
    print('')
" 2>/dev/null)

# Refresh route cache quietly
agora-code scan . --cache --quiet 2>/dev/null || true

# Track diff for the edited file (only if we got a path)
if [ -n "$FILE_PATH" ]; then
    agora-code track-diff "$FILE_PATH" 2>/dev/null || true
fi

exit 0

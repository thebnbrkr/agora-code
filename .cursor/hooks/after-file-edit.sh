#!/bin/sh
# Cursor afterFileEdit hook — track the edited file.
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

# Track diff + re-index AST so DB stays in sync with file content
if [ -n "$FILE_PATH" ]; then
    agora-code track-diff "$FILE_PATH" 2>/dev/null || true
    agora-code index "$FILE_PATH" 2>/dev/null || true
fi

exit 0

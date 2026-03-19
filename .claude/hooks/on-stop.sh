#!/bin/sh
INPUT=$(cat)

# Always checkpoint first
agora-code checkpoint --quiet 2>/dev/null || true

PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$PROMPT" ]; then exit 0; fi

python3 - "$PROMPT" << 'EOF'
import sys, subprocess, re

prompt = sys.argv[1].strip()

FILLER = re.compile(
    r'^(hi|hey|hello|ok|okay|yes|no|sure|thanks|bye|lol|cool|great|nice|yep|nope|got it)\b',
    re.I
)

def is_substantive(text):
    t = text.strip()
    if len(t) < 30:
        return False
    if FILLER.match(t):
        return False
    if t.startswith("agora-code "):
        return False
    return True

if not is_substantive(prompt):
    sys.exit(0)

subprocess.run(
    ["agora-code", "learn", prompt[:200], "--confidence", "confirmed", "--tags", "conversation-summary"],
    capture_output=True
)
EOF

exit 0

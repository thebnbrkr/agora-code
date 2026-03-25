#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
INPUT=$(cat)

# Always checkpoint first
agora-code checkpoint --quiet 2>/dev/null || true

# Use last_assistant_message from hook input directly — no JSONL parsing needed
LAST_MSG=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('last_assistant_message', ''))
except Exception:
    print('')
" 2>/dev/null)

PROMPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('prompt', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$LAST_MSG" ]; then exit 0; fi

python3 - "$LAST_MSG" "$PROMPT" << 'EOF'
import sys, subprocess, shutil, re

last_msg = sys.argv[1].strip()
prompt = sys.argv[2].strip() if len(sys.argv) > 2 else ''

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

if not is_substantive(last_msg):
    sys.exit(0)

agora_bin = "agora-code"

# Build summary from prompt (goal) + Claude's first meaningful line (finding)
first_line = last_msg.split('\n')[0][:150].strip()
summary_parts = []
if prompt and is_substantive(prompt):
    summary_parts.append(f"Session goal: {prompt[:120]}")
if first_line:
    summary_parts.append(f"Claude found: {first_line}")

if not summary_parts:
    sys.exit(0)

summary = " — ".join(summary_parts)

subprocess.run(
    [agora_bin, "learn", summary, "--confidence", "confirmed", "--tags", "conversation-summary"],
    capture_output=True
)
EOF

exit 0

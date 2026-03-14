#!/bin/sh
# Claude Code Stop hook — checkpoint session and digest conversation into memory.
#
# Fires when Claude finishes responding (end of session).
# 1. Saves a checkpoint.
# 2. Reads the transcript and stores a summary as a searchable learning.
#
# Input JSON: {"transcript_path":"...","session_id":"..."}

INPUT=$(cat)

# Always checkpoint first
agora-code checkpoint --quiet 2>/dev/null || true

# Extract transcript path
TRANSCRIPT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('transcript_path', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    exit 0
fi

# Parse transcript and store a conversation summary as a learning
python3 - "$TRANSCRIPT" << 'EOF'
import sys, json

transcript_path = sys.argv[1]

try:
    messages = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except Exception:
                continue

    # Extract user messages (the questions/goals)
    user_msgs = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user" and isinstance(content, str) and content.strip():
            user_msgs.append(content.strip()[:200])
        elif role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        user_msgs.append(text[:200])
                        break

    if not user_msgs:
        sys.exit(0)

    # Build summary: first message = goal, rest = topics covered
    goal = user_msgs[0]
    topics = list(dict.fromkeys(user_msgs[1:]))[:4]  # dedupe, max 4

    summary_parts = [f"Session goal: {goal}"]
    if topics:
        summary_parts.append("Also discussed: " + " | ".join(topics))

    summary = " — ".join(summary_parts)

    import subprocess
    subprocess.run(
        ["agora-code", "learn", summary, "--confidence", "confirmed", "--tags", "conversation-summary"],
        capture_output=True
    )

except Exception:
    pass
EOF

exit 0

#!/bin/sh
# Claude Code Stop/SessionEnd hook — checkpoint and digest conversation into memory.
#
# Fires when Claude finishes responding.
# 1. Saves a checkpoint.
# 2. Infers the real session goal from the full conversation.
# 3. Captures Claude's key findings.
# 4. Stores everything as a searchable learning for future sessions.
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

# Parse transcript, infer goal, capture findings
python3 - "$TRANSCRIPT" << 'EOF'
import sys, json, re

transcript_path = sys.argv[1]

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

    user_msgs = []
    claude_findings = []

    for m in messages:
        msg = m.get("message", m)
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, str) and content.strip():
                if not content.strip().startswith("[{"):
                    user_msgs.append(content.strip()[:200])
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            user_msgs.append(text[:200])
                            break

        elif role == "assistant":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text and len(text) > 20:
                            first_line = text.split("\n")[0][:150].strip()
                            if first_line and first_line not in claude_findings:
                                claude_findings.append(first_line)
                        break

    if not user_msgs and not claude_findings:
        sys.exit(0)

    # Infer goal: pick the most descriptive user message (longest substantive one)
    substantive = [m for m in user_msgs if is_substantive(m)]
    if substantive:
        # prefer the longest one as it likely describes the actual task
        goal = max(substantive, key=len)
    elif user_msgs:
        goal = user_msgs[0]
    else:
        goal = "unknown"

    # Topics: other substantive messages (excluding goal)
    topics = [m for m in substantive if m != goal][:3]

    # Claude findings: first 3 unique
    findings = list(dict.fromkeys(claude_findings))[:3]

    # Build summary
    summary_parts = [f"Session goal: {goal}"]
    if topics:
        summary_parts.append("Discussed: " + " | ".join(topics))
    if findings:
        summary_parts.append("Claude found: " + " | ".join(findings))

    summary = " — ".join(summary_parts)

    # Also update the checkpoint goal with the inferred goal
    import subprocess, shutil
    agora_bin = shutil.which("agora-code") or "agora-code"

    subprocess.run(
        [agora_bin, "checkpoint", "--goal", goal, "--quiet"],
        capture_output=True
    )
    subprocess.run(
        [agora_bin, "learn", summary, "--confidence", "confirmed",
         "--tags", "conversation-summary"],
        capture_output=True
    )

except Exception:
    pass
EOF

exit 0

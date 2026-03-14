#!/bin/sh
# Cursor preToolUse:Read hook — intercept large file reads and return summaries.
# Fires before every Read tool call. Small files pass through; large files get
# an AST/regex summary written to a temp file, then the read is redirected there.
#
# Input JSON from Cursor: {"tool_name":"Read","tool_input":{"file_path":"..."},...}
# Output: {"permission":"allow"} or {"permission":"allow","updated_input":{"file_path":"<summary_path>"}}
#
# Workaround: Cursor's preToolUse "deny" + "agent_message" doesn't deliver the
# message to the agent. Instead, we write the summary to a temp file and rewrite
# the Read to point there. The agent sees the summary as the file content.

INPUT=$(cat)

FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    ti = d.get('tool_input', {})
    if isinstance(ti, str):
        import json as j2
        ti = j2.loads(ti)
    print(ti.get('file_path') or ti.get('path') or '')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
    printf '{"permission":"allow"}\n'
    exit 0
fi

# ── Bypass rules — pass through without summarising ──────────────────────────
# 1. Targeted read (offset or limit present) — agent already knows the line from
#    a prior summary and is doing a surgical read. Don't intercept.
# 2. agora-code's own source — allow full reads so the agent can edit its code.
BYPASS=$(printf '%s' "$INPUT" | python3 -c "
import sys, json, os
try:
    d = json.loads(sys.stdin.read())
    ti = d.get('tool_input', {})
    if isinstance(ti, str):
        ti = json.loads(ti)
    # Rule 1: offset or limit present → targeted read
    if ti.get('offset') is not None or ti.get('limit') is not None:
        print('yes')
        sys.exit(0)
    # Rule 2: agora-code own source files
    fp = ti.get('file_path') or ti.get('path') or ''
    parts = fp.replace(os.sep, '/').split('/')
    if 'agora_code' in parts or '.cursor/hooks' in fp or '.claude/hooks' in fp:
        print('yes')
        sys.exit(0)
    print('no')
except Exception:
    print('no')
" 2>/dev/null)

if [ "$BYPASS" = "yes" ]; then
    printf '{"permission":"allow"}\n'
    exit 0
fi

RESULT=$(agora-code summarize "$FILE_PATH" --json-output 2>/dev/null)

if [ -z "$RESULT" ]; then
    printf '{"permission":"allow"}\n'
    exit 0
fi

ACTION=$(printf '%s' "$RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('action', 'allow'))
except Exception:
    print('allow')
" 2>/dev/null)

if [ "$ACTION" = "summarize" ]; then
    SUMMARY_PATH=$(printf '%s' "$RESULT" | python3 -c "
import sys, json, os, hashlib
d = json.loads(sys.stdin.read())
summary  = d.get('summary', '')
parser   = d.get('parser', 'unknown')
orig     = d.get('original_lines', 0)
orig_tok = d.get('original_tokens', orig * 4)
sum_tok  = d.get('summary_tokens', len(summary) // 4)
file_path = '$FILE_PATH'

# Generic fallback: parser couldn't extract structure.
# Serve the full file content with a prompt so the LLM can read it and
# generate a structural understanding in one pass.
if parser == 'generic':
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            raw = fh.read()
        prompt = (
            '\n\n---\n'
            '[STRUCTURAL ANALYSIS NEEDED]\n'
            f'agora-code could not parse {os.path.basename(file_path)} with tree-sitter or regex.\n'
            'After reading this file, please document its key classes, functions, and purpose\n'
            'so future reads can use a summary instead of the full content.\n'
            '---\n'
        )
        content = raw + prompt
    except Exception:
        content = summary
else:
    content = summary + '\n\n[File has ' + str(orig) + ' lines / ~' + str(orig_tok) + ' tokens. Summary is ~' + str(sum_tok) + ' tokens. To read specific sections use offset+limit.]'

summary_dir = '/tmp/agora-code-summaries'
os.makedirs(summary_dir, exist_ok=True)
name_hash = hashlib.md5(file_path.encode()).hexdigest()[:12]
basename = os.path.basename(file_path)
summary_file = os.path.join(summary_dir, f'{basename}.{name_hash}.summary.txt')

with open(summary_file, 'w') as f:
    f.write(content)
print(summary_file)
" 2>/dev/null)

    if [ -n "$SUMMARY_PATH" ] && [ -f "$SUMMARY_PATH" ]; then
        printf '{"permission":"allow","updated_input":{"file_path":"%s"}}\n' "$SUMMARY_PATH"
    else
        printf '{"permission":"allow"}\n'
    fi
else
    printf '{"permission":"allow"}\n'
fi

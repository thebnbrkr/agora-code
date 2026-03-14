#!/bin/sh
# Cursor afterShellExecution hook.
#
# Rule:
#   exit_code != 0  → pass full output (agent needs everything to debug)
#   exit_code == 0, output > 3KB → last 50 lines only (success: just the result)
#   exit_code == 0, output <= 3KB → pass through untouched ({})
#
# Input JSON: {"command":"...","stdout":"...","stderr":"...","exit_code":0,...}
# Output JSON: {"additional_context":"..."} or {}

printf '%s' "$(cat)" | python3 -c "
import sys, json

d = json.loads(sys.stdin.read())
stdout    = d.get('stdout', '') or ''
stderr    = d.get('stderr', '') or ''
cmd       = d.get('command', '') or ''
exit_code = d.get('exit_code', 0)

combined = (stdout + ('\n' + stderr if stderr.strip() else '')).strip()

if not combined:
    print('{}')
    sys.exit(0)

# Non-zero exit: give the agent everything — filtering keywords is wrong,
# different languages/tools fail differently
if exit_code != 0:
    context = (
        f'[Shell FAILED — exit={exit_code}, {len(combined.splitlines())} lines]\n'
        f'Command: {cmd}\n\n'
        + combined
    )
    print(json.dumps({'additional_context': context}))
    sys.exit(0)

# Success but large: just the last 50 lines
if len(combined) > 3000:
    lines = combined.splitlines()
    tail = '\n'.join(lines[-50:])
    n = len(lines)
    context = (
        f'[Shell OK — {n} lines, showing last 50]\n'
        f'Command: {cmd}\n\n'
        + (f'... ({n - 50} lines omitted) ...\n' if n > 50 else '')
        + tail
    )
    print(json.dumps({'additional_context': context}))
    sys.exit(0)

print('{}')
" 2>/dev/null || printf '{}\n'

#!/bin/sh
# agora-code post-install setup
# Installs Claude Code hooks and copies the skill definition so Claude Code
# picks up the agora-code skill on next session start.

set -e

# Ensure agora-code binary is available
if ! command -v agora-code >/dev/null 2>&1; then
    echo "agora-code not found — installing..."
    pip install agora-code
fi

# Install hooks (.claude/settings.json + shell scripts)
agora-code install-hooks --claude-code


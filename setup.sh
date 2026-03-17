#!/bin/sh
# agora-code post-install setup
# Installs Claude Code hooks and creates CLAUDE.md so hooks fire correctly.
# Run this once after installing the plugin:
#   sh setup.sh

set -e

# Ensure agora-code binary is available
if ! command -v agora-code >/dev/null 2>&1; then
    echo "agora-code not found — installing..."
    pip install agora-code
fi

agora-code install-hooks --claude-code

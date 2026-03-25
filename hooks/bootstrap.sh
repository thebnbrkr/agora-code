#!/bin/sh
STAMP="/tmp/agora_last_hook_$(basename "$0")"
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((NOW - LAST)) -lt 2 ]; then exit 0; fi
echo "$NOW" > "$STAMP"
# SessionStart bootstrap — install agora-code binary if missing, then inject context.

AGORA_BIN=$(which agora-code 2>/dev/null)

if [ -z "$AGORA_BIN" ]; then
    echo "[agora-code] Installing Python package (first run — this may take a moment)..."
    pip install --user "git+https://github.com/thebnbrkr/agora-code.git" --quiet 2>/dev/null

    # Find where pip installed the binary
    USER_BASE=$(python3 -m site --user-base 2>/dev/null)
    if [ -n "$USER_BASE" ] && [ -f "$USER_BASE/bin/agora-code" ]; then
        AGORA_BIN="$USER_BASE/bin/agora-code"
        echo "[agora-code] Installed at $AGORA_BIN"
        echo "[agora-code] Add to your shell profile to persist: export PATH=\"$USER_BASE/bin:\$PATH\""
        # Export PATH so all subsequent hooks in this session can find it
        if [ -n "$CLAUDE_ENV_FILE" ]; then
            echo "export PATH=\"$USER_BASE/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
        fi
    else
        AGORA_BIN=$(which agora-code 2>/dev/null)
        if [ -z "$AGORA_BIN" ]; then
            echo "[agora-code] Install failed or binary not on PATH. Run manually:"
            echo "  pip install --user git+https://github.com/thebnbrkr/agora-code.git"
            echo "  export PATH=\"\$(python3 -m site --user-base)/bin:\$PATH\""
        fi
    fi
fi

if [ -n "$AGORA_BIN" ]; then
    "$AGORA_BIN" inject --quiet 2>/dev/null || true
fi

exit 0

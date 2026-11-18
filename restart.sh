#!/usr/bin/env bash
# Restart ShiftTalk. Run it from a terminal on your desktop (not over SSH),
# so the password prompt can appear.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkill -f "$DIR/tray.py" 2>/dev/null || true
pkill -f "$DIR/shifttalk.py" 2>/dev/null || true
sleep 1
nohup /usr/bin/python3 "$DIR/tray.py" >/dev/null 2>&1 &
disown
echo "ShiftTalk restarted. Click the microphone icon > Start dictation."

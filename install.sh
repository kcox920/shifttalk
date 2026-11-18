#!/usr/bin/env bash
# ShiftTalk installer. Run it from wherever this folder lives; it sets itself up
# in place, starts at every login, and launches now. Safe to re-run.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> [1/6] System packages (asks for your sudo password)..."
sudo apt install -y python3-venv python3-evdev python3-gi \
    gir1.2-ayatanaappindicator3-0.1 pipewire-bin

echo "==> [2/6] Keyboard access (input group)..."
sudo usermod -aG input "$USER"

echo "==> [3/6] Stopping any running copy..."
pkill -f "$DIR/tray.py" 2>/dev/null || true
pkill -f "$DIR/shifttalk.py" 2>/dev/null || true
rm -f "$HOME/.config/autostart/dictation.desktop" \
      "$HOME/.config/autostart/dictation-tray.desktop"

echo "==> [4/6] Python environment + speech engine (faster-whisper)..."
python3 -m venv --system-site-packages "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet --upgrade pip faster-whisper

echo "==> [5/6] Downloading the speech model (one time, ~500 MB)..."
"$DIR/venv/bin/python" -c \
  'from faster_whisper import WhisperModel; WhisperModel("small.en", device="cpu", compute_type="int8"); print("model ready")'

echo "==> [6/6] Start at every login + launch now..."
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/shifttalk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ShiftTalk
Comment=Hold Right Shift to dictate
Exec=/usr/bin/python3 $DIR/tray.py --autostart
Icon=audio-input-microphone
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
EOF
nohup /usr/bin/python3 "$DIR/tray.py" >/dev/null 2>&1 &
disown

echo
echo "Done. A microphone icon should be in the top bar."
echo "Click it > 'Start dictation' (password prompt this one time; after your"
echo "next reboot/login it starts by itself)."
echo "Then click in any text field, hold Right Shift for half a second, speak,"
echo "and tap Right Shift to stop. The text gets typed in."

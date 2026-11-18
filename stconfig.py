"""Shared settings and microphone discovery for ShiftTalk (tray + daemon)."""
import json
import os
import re
import subprocess

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "shifttalk", "config.json")


def load_source():
    """Saved microphone (pulse/pipewire source name), or None = system default."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f).get("source") or None
    except (OSError, ValueError):
        return None


def save_source(name):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump({"source": name}, f)


def _pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True,
                          timeout=5).stdout


def list_microphones():
    """[(source_name, human description)] for real inputs (no monitors)."""
    mics = []
    name = None
    try:
        out = _pactl("list", "sources")
    except Exception:  # noqa: BLE001
        return mics
    for line in out.splitlines():
        m = re.match(r"\s*Name:\s*(.+)", line)
        if m:
            name = m.group(1).strip()
            continue
        m = re.match(r"\s*Description:\s*(.+)", line)
        if m and name:
            if not name.endswith(".monitor"):
                mics.append((name, m.group(1).strip()))
            name = None
    return mics


def default_source_name():
    try:
        return _pactl("get-default-source").strip()
    except Exception:  # noqa: BLE001
        return ""


def effective_source():
    """The saved mic if it is still plugged in, else None (system default)."""
    saved = load_source()
    if saved and any(n == saved for n, _ in list_microphones()):
        return saved
    return None

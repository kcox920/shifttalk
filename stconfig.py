"""Shared settings and microphone discovery for ShiftTalk (tray + daemon)."""
import json
import os
import re
import shutil
import subprocess

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "shifttalk", "config.json")


def load_source():
    """Saved microphone (pulse/pipewire source name), or None = system default."""
    return _read_config().get("source") or None


def save_source(name):
    cfg = _read_config()
    cfg["source"] = name
    _write_config(cfg)


def _pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True,
                          timeout=5).stdout


def _list_microphones_pw():
    """PipeWire-only systems (no pactl): Audio/Source nodes from pw-dump."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True,
                             timeout=5).stdout
        nodes = json.loads(out)
    except Exception:  # noqa: BLE001
        return []
    mics = []
    for o in nodes:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = o.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Source" and props.get("node.name"):
            mics.append((props["node.name"],
                         props.get("node.description") or props["node.name"]))
    return mics


def list_microphones():
    """[(source_name, human description)] for real inputs (no monitors)."""
    if not shutil.which("pactl"):
        return _list_microphones_pw()
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


# ---------- speech engine: offline (faster-whisper) or online ----------
ENGINES = [
    ("offline", "Offline (on this computer)"),
    ("openai", "Online (OpenAI)"),
]
KEY_ENV = {"openai": "OPENAI_API_KEY"}


def _read_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)


def load_engine():
    eng = _read_config().get("engine")
    return eng if eng in dict(ENGINES) else "offline"


def save_engine(engine):
    cfg = _read_config()
    cfg["engine"] = engine
    _write_config(cfg)


def api_key(engine):
    """API key for an online engine, or None. Never stored by ShiftTalk: read
    from the environment, then ~/.config/shifttalk/<engine>.key, then the
    `export NAME=...` line in ~/.bashrc (so it also works when started from the
    GNOME autostart, which does not read .bashrc)."""
    name = KEY_ENV.get(engine)
    if not name:
        return None
    if os.environ.get(name):
        return os.environ[name].strip()
    for path in (os.path.join(os.path.dirname(CONFIG_PATH), engine + ".key"),):
        try:
            with open(path) as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            pass
    try:
        with open(os.path.expanduser("~/.bashrc")) as f:
            for line in f:
                m = re.match(r"\s*export\s+" + name + r"=(.*)", line)
                if m:
                    v = m.group(1).strip().strip("'\"")
                    if v:
                        return v
    except OSError:
        pass
    return None


def engine_available(engine):
    return engine == "offline" or bool(api_key(engine))


def effective_engine():
    """Saved engine if usable right now, else offline."""
    eng = load_engine()
    return eng if engine_available(eng) else "offline"

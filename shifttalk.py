#!/usr/bin/env python3
"""ShiftTalk daemon. Right-Shift hold-to-dictate: records, transcribes locally, types the text.

- Hold Right Shift for HOLD_SECONDS (no other key pressed) -> start recording.
- Press Right Shift while recording                         -> stop, transcribe, type.
Everything runs locally (faster-whisper). Text is typed into the focused window
through a virtual keyboard (uinput), so it works in Firefox, Chrome, anything.
Requires: python3-evdev, faster-whisper, pw-record (PipeWire) or parecord (PulseAudio), `input` group membership.
Prints "PID n" and "STATE x" lines on stdout for the tray utility.
"""
import asyncio
import os
import signal
import subprocess
import tempfile
import time
import threading
import unicodedata

import evdev

import stconfig
from evdev import UInput, ecodes as e

HOTKEY = e.KEY_RIGHTSHIFT
HOLD_SECONDS = 0.5
MODEL_NAME = "small.en"
SOUND_DIR = "/usr/share/sounds/freedesktop/stereo"
SND_START = f"{SOUND_DIR}/device-added.oga"
SND_STOP = f"{SOUND_DIR}/device-removed.oga"
SND_DONE = f"{SOUND_DIR}/complete.oga"
KEY_DELAY = 0.004
TAIL_GRACE = 0.3        # keep recording this long after the stop tap (mic buffers lag)
PAD_SECONDS = 0.4       # silence added before/after audio: Whisper drops abrupt first words
ONLINE_MODEL = "gpt-4o-mini-transcribe"
ONLINE_URL = "https://api.openai.com/v1/audio/transcriptions"
ONLINE_TIMEOUT = 25

state = "loading"          # loading | idle | recording | busy
timer = None
rec_proc = None
rec_path = None
model = None
model_ready = threading.Event()
typer = None


def say(s):
    print(s, flush=True)


def set_state(s):
    global state
    state = s
    say(f"STATE {'ready' if s == 'idle' else s}")


_pipewire = None


def use_pipewire():
    """PipeWire tools only work when a PipeWire server is running; plain
    PulseAudio systems (e.g. older Ubuntu) need parecord/paplay instead.
    Checked lazily and only cached on success, so starting before the audio
    server is up (early at login) cannot lock in the wrong backend."""
    global _pipewire
    if _pipewire is None:
        try:
            out = subprocess.run(["pactl", "info"], capture_output=True,
                                 text=True, timeout=5)
            if out.returncode == 0 and "Server Name" in out.stdout:
                _pipewire = "pipewire" in out.stdout.lower()
        except Exception:  # noqa: BLE001
            pass
    return bool(_pipewire)


def beep(path):
    player = "pw-play" if use_pipewire() else "paplay"
    subprocess.Popen([player, path], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


# ---------- typing through uinput (US layout) ----------
def build_keymap():
    km = {" ": (e.KEY_SPACE, False)}
    for c in "abcdefghijklmnopqrstuvwxyz":
        k = getattr(e, f"KEY_{c.upper()}")
        km[c] = (k, False)
        km[c.upper()] = (k, True)
    for d, s in zip("0123456789", ")!@#$%^&*("):
        k = getattr(e, f"KEY_{d}")
        km[d] = (k, False)
        km[s] = (k, True)
    pairs = [("-", "_", e.KEY_MINUS), ("=", "+", e.KEY_EQUAL),
             ("[", "{", e.KEY_LEFTBRACE), ("]", "}", e.KEY_RIGHTBRACE),
             ("\\", "|", e.KEY_BACKSLASH), (";", ":", e.KEY_SEMICOLON),
             ("'", '"', e.KEY_APOSTROPHE), (",", "<", e.KEY_COMMA),
             (".", ">", e.KEY_DOT), ("/", "?", e.KEY_SLASH),
             ("`", "~", e.KEY_GRAVE)]
    for lo, hi, k in pairs:
        km[lo] = (k, False)
        km[hi] = (k, True)
    return km


KEYMAP = build_keymap()
REPLACE = {"‘": "'", "’": "'", "“": '"', "”": '"',
           "–": "-", "—": "-", "…": "...", "\n": " ",
           "\t": " ", " ": " "}


def to_typable(text):
    out = []
    for ch in text:
        ch = REPLACE.get(ch, ch)
        for c in unicodedata.normalize("NFKD", ch):
            if c in KEYMAP:
                out.append(c)
            elif c in REPLACE.values():
                out.extend(x for x in c if x in KEYMAP)
    return "".join(out)


def type_text(text):
    import time
    for ch in to_typable(text):
        key, shifted = KEYMAP[ch]
        if shifted:
            typer.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            typer.syn()
        typer.write(e.EV_KEY, key, 1)
        typer.syn()
        typer.write(e.EV_KEY, key, 0)
        typer.syn()
        if shifted:
            typer.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            typer.syn()
        time.sleep(KEY_DELAY)


# ---------- model, recording, transcription ----------
def load_model():
    global model
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        model_ready.set()
        if state == "loading":
            set_state("idle")
    except Exception as ex:  # noqa: BLE001
        print(f"Could not load speech model: {ex}", flush=True)
        os._exit(2)


def spawn_recorder():
    """Start the recorder process. Called on Right Shift *press* (before the hold
    time is up) so the mic hardware is already delivering audio when the chime
    sounds; if the key is released early the recording is thrown away."""
    global rec_proc, rec_path
    fd, rec_path = tempfile.mkstemp(prefix="dictation-", suffix=".wav")
    os.close(fd)
    mic = stconfig.effective_source()   # None = system default
    if use_pipewire():
        cmd = ["pw-record", "--rate", "16000", "--channels", "1",
               "--format", "s16"]
        if mic:
            cmd += ["--target", mic]
        cmd.append(rec_path)
    else:
        cmd = ["parecord", "--rate=16000", "--channels=1",
               "--format=s16le", "--file-format=wav"]
        if mic:
            cmd.append(f"--device={mic}")
        cmd.append(rec_path)
    rec_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)


def start_recording():
    """Spawn (if not already armed) and announce: chime + red icon."""
    if rec_proc is None:
        spawn_recorder()
    beep(SND_START)
    set_state("recording")


def discard_recording():
    path = stop_recording()
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def stop_recording():
    """Stops pw-record; returns the wav path."""
    global rec_proc, rec_path
    proc, path = rec_proc, rec_path
    rec_proc = rec_path = None
    if proc:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return path


def load_audio(path):
    """Reads our 16 kHz mono wav directly (avoids faster-whisper's PyAV decoder)."""
    import wave
    import numpy as np
    with wave.open(path) as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def pad_audio(a):
    import numpy as np
    pad = np.zeros(int(16000 * PAD_SECONDS), dtype=np.float32)
    return np.concatenate([pad, a, pad])


def wav_bytes(a):
    import io
    import wave
    import numpy as np
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def transcribe_online(a):
    """OpenAI speech-to-text. Raises on any failure so the caller can fall back."""
    import json
    import urllib.request
    key = stconfig.api_key("openai")
    if not key:
        raise RuntimeError("no OpenAI API key")
    boundary = "----shifttalk" + os.urandom(8).hex()
    parts = []
    for name, val in (("model", ONLINE_MODEL), ("language", "en"),
                      ("response_format", "json")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="{name}"\r\n\r\n{val}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="dictation.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.append(wav_bytes(a))
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        ONLINE_URL, data=b"".join(parts),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=ONLINE_TIMEOUT) as r:
        return json.load(r).get("text", "").strip()


def online_error(ex):
    """Short human reason for an online failure (never includes the key)."""
    import json
    import urllib.error
    if isinstance(ex, urllib.error.HTTPError):
        try:
            code = json.loads(ex.read().decode()).get("error", {}).get("code", "")
        except Exception:  # noqa: BLE001
            code = ""
        return {"credit_balance_exhausted": "OpenAI account has no credits",
                "insufficient_quota": "OpenAI account has no credits",
                "invalid_api_key": "invalid OpenAI API key"}.get(
                    code, f"HTTP {ex.code}")
    if isinstance(ex, urllib.error.URLError):
        return "no network"
    return str(ex) or type(ex).__name__


def transcribe_offline(a):
    model_ready.wait()
    segments, _ = model.transcribe(a, language="en", beam_size=3, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_and_type(path, loop):
    try:
        time.sleep(TAIL_GRACE)           # let the mic buffer drain, then stop
        path = stop_recording() or path
        audio = pad_audio(load_audio(path))
        text = None
        if stconfig.effective_engine() == "openai":
            try:
                text = transcribe_online(audio)
            except Exception as ex:  # noqa: BLE001
                say(f"INFO Online failed ({online_error(ex)}); used offline")
        if text is None:
            text = transcribe_offline(audio)
        if text:
            type_text(text)
            beep(SND_DONE)
    except Exception as ex:  # noqa: BLE001
        print(f"Transcription failed: {ex}", flush=True)
    finally:
        try:
            os.unlink(path)
        except (OSError, TypeError):
            pass
        loop.call_soon_threadsafe(set_state, "idle")


# ---------- hotkey watching ----------
def cancel_timer():
    global timer
    if timer is not None:
        timer.cancel()
        timer = None


def fire_start():
    global timer
    timer = None
    if state in ("idle", "loading"):
        start_recording()


def abort_arming():
    """Right Shift released (or another key pressed) before the hold time."""
    cancel_timer()
    if state in ("idle", "loading"):
        discard_recording()


async def watch(dev, loop):
    global timer
    async for ev in dev.async_read_loop():
        if ev.type != e.EV_KEY or ev.value == 2:
            continue
        if ev.code == HOTKEY:
            if ev.value == 1:
                if state == "recording":
                    beep(SND_STOP)
                    set_state("busy")
                    # recorder keeps running for TAIL_GRACE inside the worker
                    loop.run_in_executor(None, transcribe_and_type, rec_path, loop)
                elif state in ("idle", "loading"):
                    cancel_timer()
                    if rec_proc is None:
                        spawn_recorder()
                    timer = loop.call_later(HOLD_SECONDS, fire_start)
            elif ev.value == 0:
                abort_arming()
        elif ev.value == 1:
            abort_arming()


def cleanup(*_):
    stop_recording()
    os._exit(0)


async def main():
    global typer
    say(f"PID {os.getpid()}")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, cleanup)

    devices = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        keys = dev.capabilities().get(e.EV_KEY, [])
        if HOTKEY in keys and e.KEY_A in keys and not dev.name.startswith("dictation"):
            devices.append(dev)
    if not devices:
        raise SystemExit("No keyboards found (is the 'input' group active?)")

    needed = {k for k, _ in KEYMAP.values()} | {e.KEY_LEFTSHIFT}
    typer = UInput({e.EV_KEY: sorted(needed)}, name="dictation-typer")

    say("STATE loading")
    threading.Thread(target=load_model, daemon=True).start()
    await asyncio.gather(*(watch(d, loop) for d in devices))


if __name__ == "__main__":
    asyncio.run(main())

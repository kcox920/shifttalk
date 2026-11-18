#!/usr/bin/env python3
"""ShiftTalk daemon. Right-Shift hold-to-dictate: records, transcribes locally, types the text.

- Hold Right Shift for HOLD_SECONDS (no other key pressed) -> start recording.
- Press Right Shift while recording                         -> stop, transcribe, type.
Everything runs locally (faster-whisper). Text is typed into the focused window
through a virtual keyboard (uinput), so it works in Firefox, Chrome, anything.
Requires: python3-evdev, faster-whisper, pw-record, `input` group membership.
Prints "PID n" and "STATE x" lines on stdout for the tray utility.
"""
import asyncio
import os
import signal
import subprocess
import tempfile
import threading
import unicodedata

import evdev
from evdev import UInput, ecodes as e

HOTKEY = e.KEY_RIGHTSHIFT
HOLD_SECONDS = 0.5
MODEL_NAME = "small.en"
SOUND_DIR = "/usr/share/sounds/freedesktop/stereo"
SND_START = f"{SOUND_DIR}/device-added.oga"
SND_STOP = f"{SOUND_DIR}/device-removed.oga"
SND_DONE = f"{SOUND_DIR}/complete.oga"
KEY_DELAY = 0.004

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


def beep(path):
    subprocess.Popen(["pw-play", path], stdout=subprocess.DEVNULL,
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


def start_recording():
    global rec_proc, rec_path
    fd, rec_path = tempfile.mkstemp(prefix="dictation-", suffix=".wav")
    os.close(fd)
    rec_proc = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1",
         "--format", "s16", rec_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    beep(SND_START)
    set_state("recording")


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


def transcribe_and_type(path, loop):
    try:
        model_ready.wait()
        segments, _ = model.transcribe(load_audio(path), language="en",
                                       beam_size=3, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            type_text(text)
            beep(SND_DONE)
    except Exception as ex:  # noqa: BLE001
        print(f"Transcription failed: {ex}", flush=True)
    finally:
        try:
            os.unlink(path)
        except OSError:
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
    if state == "idle" or state == "loading":
        start_recording()


async def watch(dev, loop):
    global timer
    async for ev in dev.async_read_loop():
        if ev.type != e.EV_KEY:
            continue
        if ev.code == HOTKEY:
            if ev.value == 1:
                if state == "recording":
                    path = stop_recording()
                    beep(SND_STOP)
                    set_state("busy")
                    loop.run_in_executor(None, transcribe_and_type, path, loop)
                elif state in ("idle", "loading"):
                    cancel_timer()
                    timer = loop.call_later(HOLD_SECONDS, fire_start)
            elif ev.value == 0:
                cancel_timer()
        elif ev.value == 1:
            cancel_timer()


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


asyncio.run(main())

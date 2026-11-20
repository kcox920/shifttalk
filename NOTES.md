# ShiftTalk: design notes and findings

Push-to-talk dictation for Linux: hold Right Shift, speak, tap Right Shift, text is typed
into the focused window through a virtual keyboard (uinput). See `README.md` for usage.

## Why Right Shift
It is almost never used for typing (people reach for Left Shift), and requiring a 0.5 s hold
means ordinary capital letters never trigger it. Tapping it or pressing any other key
before the hold completes cancels (the early recording is discarded).

## Moving parts
| File | Role |
|---|---|
| `shifttalk.py` | Daemon: watches the keyboard (evdev), records, transcribes, types. Prints `PID`/`STATE`/`INFO` lines for the tray. |
| `tray.py` | GNOME top-bar indicator: start/stop, Microphone picker, Speech engine picker, status. |
| `stconfig.py` | Shared settings (`~/.config/shifttalk/config.json`), mic discovery, engine/API-key lookup. |
| `install.sh` | Idempotent installer. Uses sudo only when a package or the `input` group is actually missing. |

## Audio: what we measured (do not regress these)
- **PulseAudio `parecord` loses the end of the recording on stop.** With default buffering,
  SIGINT discards the unflushed buffer: a 5.2 s recording returned ~3.8 s (last 1-2 s of
  speech lost). Found by recording the real mic and the lossless speaker monitor together
  while playing a beep train. Fix: `--latency-msec=20`, plus a 0.3 s `TAIL_GRACE` after the
  stop tap.
- **PipeWire `pw-record` barely loses anything** (0.01-0.08 s measured). It gets
  `--latency 20ms` too.
- Backend is chosen by what exists: no `pactl` means PipeWire tools only (mics via
  `pw-dump`, chimes via `pw-play`); otherwise `pactl info` decides. Detection is lazy and
  only cached on success, because the audio server may not be up yet at login.
- Recording starts on the key *press*, not after the hold, so audio is already flowing
  when the chime sounds. 0.4 s of silence is padded around the clip because Whisper drops
  abrupt first words.

## Engines
- **Offline (default):** faster-whisper `small.en`, int8 on CPU (about 4 threads).
- **Online:** OpenAI `gpt-4o-mini-transcribe` (key from `$OPENAI_API_KEY`, then
  `~/.config/shifttalk/openai.key`, then the `export` line in `~/.bashrc`; never stored).
  On any failure it falls back to offline and the tray status line says why. On a test
  clip it took 1.4 s vs 3.7 s offline and heard the opening words offline missed.
- No keyless online option is both reliable and clearly allowed (the unofficial Google
  endpoint is undocumented; Chrome's Web Speech needs a browser tab).

## GNOME tray gotchas (learned the hard way)
- GNOME caches top-bar icons **by filename**: changing an SVG's contents is not enough,
  give it a new name (hence `shifttalk-*-*.svg`).
- Never rebuild a submenu while the shell is showing it (menu came up empty). Build menu
  items once, update label/sensitivity/selection in place, rebuild only when the set of
  mics changes, and poll gently (5 s).
- Autostart runs before the audio server may be ready: `X-GNOME-Autostart-Delay=15`.

## Known limits
US keyboard layout only; non-ASCII is flattened (e to e). The user needs raw keyboard
access (`input` group, or `plugdev` where it owns `/dev/input` and `/dev/uinput`); anything
with that access can read keystrokes, so install only on machines you trust.

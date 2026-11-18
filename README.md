# ShiftTalk

Hold **Right Shift**, talk, tap Right Shift, and your words are typed into
whatever text field has focus (Firefox, Chrome, a terminal, anything).
Speech recognition runs locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper);
no audio leaves your machine. A microphone icon in the GNOME top bar starts,
stops and shows status.

## Install

```
./install.sh
```

It asks for your sudo password once, installs the packages, downloads the
speech model (~500 MB, one time), sets ShiftTalk to start at every login, and
launches it. Safe to re-run.

## Use

- Click the top-bar mic icon > **Start dictation**.
- Click in a text field. **Hold Right Shift for 0.5 s** (chime), speak.
- **Tap Right Shift** to stop (chime). After a second or two the text is typed
  in (done chime).
- **Choose a microphone:** top-bar mic icon > **Microphone**. It lists the inputs
  present right now (just one entry if there is only one); with several you also get
  **System default**. The choice is saved in `~/.config/shifttalk/config.json` and
  applies to the next recording, no restart. If the saved mic is unplugged it falls
  back to the system default.
- Grey struck-through mic = stopped. Red mic = running. Red disc = recording.

Right Shift used normally (for capital letters) is unaffected.

## Files

| File | Purpose |
|------|---------|
| `shifttalk.py` | Daemon: watches Right Shift, records, transcribes, types. |
| `stconfig.py` | Shared settings + microphone discovery (tray and daemon). |
| `tray.py` | GNOME top-bar utility (start/stop/status). |
| `icons/` | Tray icons (stopped / running / recording). |
| `install.sh` | One-shot installer. |
| `restart.sh` | Restart the tray from a desktop terminal. |

## Requirements and caveats

- Ubuntu/GNOME on Wayland with the AppIndicator extension (on by default in
  Ubuntu), PipeWire for audio.
- **Security:** ShiftTalk needs your user in the `input` group to read the
  keyboard and create a virtual keyboard. Anything running as you with that
  group can read keystrokes, so only install this on a machine you trust.
- Until your first login after install, the group isn't active in your
  session, so **Start** shows a one-time password prompt (via `pkexec`). After
  that it starts automatically at login.
- Typing assumes a **US keyboard layout**; non-ASCII characters are simplified
  (é becomes e).
- The model is English-only (`small.en`). Change `MODEL_NAME` in `shifttalk.py`
  for a multilingual or larger model (slower).
- Transcription runs on the CPU, so expect a short delay after you stop.

## License

MIT. See `LICENSE`.

## Settings

Edit the constants at the top of `shifttalk.py`: `HOTKEY`, `HOLD_SECONDS`,
`MODEL_NAME`, and the chime sounds.

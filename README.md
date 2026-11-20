# ShiftTalk

Hold **Right Shift**, talk, tap Right Shift, and your words are typed into
whatever text field has focus (Firefox, Chrome, a terminal, anything).
Speech recognition runs locally by default with [faster-whisper](https://github.com/SYSTRAN/faster-whisper);
no audio leaves your machine unless you pick the **Online** engine (see below). A microphone icon in the GNOME top bar starts,
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
- **Speech engine:** top-bar mic icon > **Speech engine**.
  - **Offline** (default): faster-whisper on this computer. Private, free.
  - **Online (OpenAI)**: sends each recording to OpenAI (`gpt-4o-mini-transcribe`).
    Faster and more accurate, but your audio leaves the machine and it costs API credit.
    The key is never stored by ShiftTalk: it uses `$OPENAI_API_KEY`, else
    `~/.config/shifttalk/openai.key`, else the `export OPENAI_API_KEY=` line in `~/.bashrc`
    (the last one so it works from the GNOME autostart). With no key the entry is greyed out.
  - If an online request fails (no network, no credits, bad key) the recording is
    transcribed offline instead and the menu's status line says why for 10 s.
- Grey struck-through mic = stopped. Green mic = running, waiting. Very bright red
  disc = recording or transcribing; back to green once the text is typed.

Right Shift used normally (for capital letters) is unaffected.

## Files

| File | Purpose |
|------|---------|
| `shifttalk.py` | Daemon: watches Right Shift, records, transcribes, types. |
| `stconfig.py` | Shared settings + microphone discovery (tray and daemon). |
| `tray.py` | GNOME top-bar utility (start/stop/status). |
| `icons/` | Tray icons: `shifttalk-off-grey`, `shifttalk-on-green`, `shifttalk-active-red`. (Renamed from dictation-*.svg because GNOME caches top-bar icons by filename.) |
| `install.sh` | One-shot installer. |
| `restart.sh` | Restart the tray from a desktop terminal. |

## Audio timing (why the last words used to go missing)

`parecord`'s default buffering is large, and when it is stopped (SIGINT) it throws away
whatever is still in the buffer: a 5 s recording came back as ~3.8 s, so the **last 1-2
seconds of speech were lost**. Measured by recording the mic and the (lossless) speaker
monitor together while playing beeps. Fix: `parecord --latency-msec=20`, and the
recorder also keeps running `TAIL_GRACE` (0.3 s) after the stop tap. PipeWire's
`pw-record` (measured on Ubuntu 26.04) only lost 0.01-0.08 s; it now also gets `--latency 20ms`.
On PipeWire-only systems (no `pactl`) the mic list comes from `pw-dump` and chimes from `pw-play`.

Recording also starts at the Right Shift *press* (thrown away if you let go before the
chime or hit another key), and 0.4 s of silence is padded around the clip because
Whisper tends to drop abrupt first/last words.

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

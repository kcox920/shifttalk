#!/usr/bin/env python3
"""ShiftTalk: GNOME top-bar utility to start/stop Right-Shift voice dictation.

Menu: status line, Start/Stop toggle, Microphone picker, Speech engine picker, Quit.
Grey struck-through mic = stopped; green = running; very bright red = recording/transcribing.
Launched with --autostart (at login) it also starts dictation automatically,
provided the `input` group is active; otherwise it waits for you to click Start
(which then asks for your password via pkexec).
"""
import grp
import os
import signal
import subprocess
import sys
import threading

import stconfig

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import GLib, Gtk, AyatanaAppIndicator3 as AppIndicator

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(BASE, "venv", "bin", "python")
DAEMON = os.path.join(BASE, "shifttalk.py")
ICON_DIR = os.path.join(BASE, "icons")
ICON_OFF = os.path.join(ICON_DIR, "shifttalk-off-grey.svg")      # grey, struck through
ICON_READY = os.path.join(ICON_DIR, "shifttalk-on-green.svg")   # green mic (running, idle)
ICON_LISTENING = os.path.join(ICON_DIR, "shifttalk-active-red.svg")  # very bright red disc (recording / transcribing)
PASS_ENV = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY",
            "DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE", "HOME", "PATH")


def has_input_group():
    """True if we can already read the keyboard and create a virtual one
    (via the `input` group, or e.g. `plugdev` on some systems)."""
    try:
        if grp.getgrnam("input").gr_gid in os.getgroups():
            return True
    except KeyError:
        pass
    return os.access("/dev/uinput", os.R_OK | os.W_OK) and os.access(
        "/dev/input/event0", os.R_OK)


def build_command():
    if has_input_group():
        return [PYTHON, DAEMON]
    env = [f"{k}={os.environ[k]}" for k in PASS_ENV if k in os.environ]
    return ["pkexec", "setpriv", f"--reuid={os.getuid()}",
            f"--regid={os.getgid()}", "--init-groups", "env", *env,
            PYTHON, DAEMON]


class Tray:
    def __init__(self):
        self.proc = None
        self.pid = None
        self.ind = AppIndicator.Indicator.new(
            "shifttalk", ICON_OFF,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="")
        self.status_item.set_sensitive(False)
        self.toggle_item = Gtk.MenuItem(label="Start dictation")
        self.toggle_item.connect("activate", self.on_toggle)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.on_quit)
        self.mic_item = Gtk.MenuItem(label="Microphone")
        self.mic_menu = Gtk.Menu()
        self.mic_item.set_submenu(self.mic_menu)
        self._updating = False       # True while we change items in place
        self._mic_sig = None
        self.refresh_mics()
        self.engine_item = Gtk.MenuItem(label="Speech engine")
        self.engine_menu = Gtk.Menu()
        self.engine_item.set_submenu(self.engine_menu)
        self.engine_items = {}
        self.refresh_engines()
        # Never rebuild a submenu while the shell is showing it (that made the
        # Speech engine menu come up empty). Instead poll gently and only touch
        # what actually changed.
        GLib.timeout_add_seconds(5, self.poll_menus)
        for item in (self.status_item, self.toggle_item, self.mic_item,
                     self.engine_item,
                     Gtk.SeparatorMenuItem(), quit_item):
            menu.append(item)
        menu.show_all()
        self.ind.set_menu(menu)
        self.set_state("off")

    def poll_menus(self):
        self.refresh_mics()
        self.refresh_engines()
        return True

    def refresh_mics(self):
        """Rebuild the Microphone submenu only when the set of inputs changed;
        otherwise just move the selection."""
        mics = stconfig.list_microphones()
        current = stconfig.effective_source()
        sig = tuple(n for n, _ in mics)
        if sig != self._mic_sig:
            self._mic_sig = sig
            self._mic_items = {}
            for child in self.mic_menu.get_children():
                self.mic_menu.remove(child)
            group = None
            entries = ([(None, "System default")] if len(mics) > 1 else []) + mics
            if not entries:
                none = Gtk.MenuItem(label="No microphone found")
                none.set_sensitive(False)
                self.mic_menu.append(none)
            for name, label in entries:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
                group = group or item
                item.connect("toggled", self.on_mic_toggled, name)
                self.mic_menu.append(item)
                self._mic_items[name] = item
            self.mic_menu.show_all()
        self._updating = True
        for name, item in self._mic_items.items():
            # one mic: it is always the selection; several: the saved one
            want = True if len(mics) <= 1 else (name == current)
            if item.get_active() != want:
                item.set_active(want)
        self._updating = False

    def refresh_engines(self):
        """Items are created once; labels / greyed-out / selection updated in place."""
        if not self.engine_items:
            group = None
            for eng, label in stconfig.ENGINES:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
                group = group or item
                item.connect("toggled", self.on_engine_toggled, eng)
                self.engine_menu.append(item)
                self.engine_items[eng] = item
            self.engine_menu.show_all()
        current = stconfig.effective_engine()
        self._updating = True
        for eng, label in stconfig.ENGINES:
            item = self.engine_items[eng]
            ok = stconfig.engine_available(eng)
            text = label if ok else f"{label} (no API key)"
            if item.get_label() != text:
                item.set_label(text)
            if item.get_sensitive() != ok:
                item.set_sensitive(ok)
            if item.get_active() != (eng == current):
                item.set_active(eng == current)
        self._updating = False

    def on_engine_toggled(self, item, eng):
        if item.get_active() and not self._updating:
            stconfig.save_engine(eng)

    def show_info(self, text):
        """Temporary status-line message from the daemon (e.g. online fallback)."""
        self.status_item.set_label(text)
        GLib.timeout_add_seconds(10, self.restore_status)

    def restore_status(self):
        self.set_state(self.state)
        return False

    def on_mic_toggled(self, item, name):
        if item.get_active() and not self._updating:
            stconfig.save_source(name)

    def set_state(self, state, detail=""):
        icon, text = {
            "off": (ICON_OFF, "Stopped"),
            "starting": (ICON_OFF, "Starting..."),
            "loading": (ICON_OFF, "Loading speech model..."),
            "ready": (ICON_READY, "Running: hold Right Shift to dictate"),
            "recording": (ICON_LISTENING, "Recording... tap Right Shift to stop"),
            "busy": (ICON_LISTENING, "Transcribing..."),
            "error": (ICON_OFF, f"Error: {detail}"),
        }.get(state, (ICON_READY, state))
        self.state = state
        self.ind.set_icon_full(icon, text)
        self.status_item.set_label(text)
        running = state not in ("off", "error")
        self.toggle_item.set_label(
            "Stop dictation" if running else "Start dictation")

    def start(self):
        if self.proc:
            return
        self.set_state("starting")
        self.proc = subprocess.Popen(
            build_command(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self.read_output, args=(self.proc,),
                         daemon=True).start()

    def read_output(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PID "):
                GLib.idle_add(self.on_pid, proc, int(line[4:]))
            elif line.startswith("STATE "):
                GLib.idle_add(self.on_daemon_state, proc, line[6:])
            elif line.startswith("INFO "):
                GLib.idle_add(self.show_info, line[5:])
        err = proc.stderr.read().strip().splitlines()
        proc.wait()
        GLib.idle_add(self.on_daemon_exit, proc, proc.returncode,
                      err[-1] if err else "")

    def on_pid(self, proc, pid):
        if proc is self.proc:
            self.pid = pid
        return False

    def on_daemon_state(self, proc, state):
        if proc is self.proc:
            self.set_state(state)
        return False

    def on_daemon_exit(self, proc, code, last_err):
        if proc is not self.proc:
            return False
        self.proc = self.pid = None
        if code in (0, -signal.SIGTERM, 143):
            self.set_state("off")
        elif code in (126, 127) and not last_err:
            self.set_state("error", "authentication cancelled")
        else:
            self.set_state("error", last_err or f"exited with code {code}")
        return False

    def stop(self):
        proc, pid = self.proc, self.pid
        self.proc = self.pid = None
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif proc:
            try:
                proc.terminate()
            except PermissionError:
                pass
        self.set_state("off")

    def on_toggle(self, _item):
        self.stop() if self.proc else self.start()

    def on_quit(self, _item):
        self.stop()
        Gtk.main_quit()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    tray = Tray()
    if "--autostart" in sys.argv and has_input_group():
        tray.start()
    Gtk.main()

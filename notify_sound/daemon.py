import os
import re
import signal
import subprocess
import sys
import threading

from gi.repository import GLib

from . import config, player

MONITOR_RULE = (
    "eavesdrop=true,type='method_call',"
    "interface='org.freedesktop.Notifications',"
    "member='Notify'"
)

_DICT_KEY_RE = re.compile(r'dict entry\(\s+string "([^"]+)"')
_STRING_RE = re.compile(r'^\s+string "([^"]*)"', re.MULTILINE)


class NotifyDaemon:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.monitor = None
        self.seen = set(config.load_state().get("apps_seen", []))
        self.lock = threading.Lock()

    def _start_monitor(self):
        if self.monitor is not None and self.monitor.stdout:
            try:
                self.monitor.stdout.close()
            except Exception:
                pass
        try:
            self.monitor = subprocess.Popen(
                ["dbus-monitor", MONITOR_RULE],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            sys.stderr.write(
                "NotifySound: dbus-monitor no está instalado "
                "(instala el paquete 'dbus').\n"
            )
            return
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buffer = None
        for raw in self.monitor.stdout:
            text = raw.decode(errors="replace")
            if text.startswith("method call") and "member=Notify" in text:
                if buffer is not None:
                    self._handle_block(buffer)
                buffer = [text]
            elif buffer is not None:
                buffer.append(text)
        if self.loop.is_running():
            GLib.idle_add(self._start_monitor)

    def _handle_block(self, lines):
        block = "".join(lines)
        string_match = _STRING_RE.search(block)
        if not string_match:
            return
        app_name = string_match.group(1)
        hints = set(_DICT_KEY_RE.findall(block))
        if "x-shell-sender" in hints:
            return
        self._record_app(app_name)
        self._maybe_play(app_name, hints)

    def _record_app(self, app_name):
        if not app_name:
            return
        with self.lock:
            if app_name not in self.seen:
                self.seen.add(app_name)
                state = config.load_state()
                merged = sorted(set(state.get("apps_seen", [])) | self.seen)
                config.save_state({"apps_seen": merged})

    def _maybe_play(self, app_name, hints):
        cfg = config.load_config()
        if not cfg.get("enabled", True):
            return
        app_cfg = cfg.get("apps", {}).get(app_name, {})
        if app_cfg.get("enabled") is False:
            return
        if ("sound-file" in hints or "sound-name" in hints) and cfg.get(
            "no_duplicate", True
        ):
            return
        choice = app_cfg.get("sound") or cfg.get("sound")
        if choice:
            player.play_choice(choice)

    def on_signal(self, signum, frame):
        self.stop()

    def stop(self):
        if self.loop.is_running():
            self.loop.quit()

    def run(self):
        self._start_monitor()
        self.loop.run()
        if self.monitor:
            self.monitor.terminate()


def main_daemon():
    if config.is_running():
        print("NotifySound: el daemon ya está corriendo.")
        return 1
    os.makedirs(os.path.dirname(config.PID_FILE), exist_ok=True)
    with open(config.PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    daemon = NotifyDaemon()
    signal.signal(signal.SIGTERM, daemon.on_signal)
    signal.signal(signal.SIGINT, daemon.on_signal)
    try:
        daemon.run()
    finally:
        try:
            os.unlink(config.PID_FILE)
        except FileNotFoundError:
            pass
    return 0


def quit_daemon():
    pid = config.read_pid()
    if pid is None or not config.is_running():
        print("NotifySound: el daemon no está corriendo.")
        try:
            os.unlink(config.PID_FILE)
        except FileNotFoundError:
            pass
        return 1
    os.kill(pid, signal.SIGTERM)
    print("NotifySound: daemon detenido.")
    return 0

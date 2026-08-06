import os
import re
import signal
import subprocess
import sys
import threading
import time

from gi.repository import GLib

from . import config, player

MONITOR_RULE = (
    "eavesdrop=true,type='method_call',"
    "interface='org.freedesktop.Notifications',"
    "member='Notify'"
)

_DICT_KEY_RE = re.compile(r'dict entry\(\s+string "([^"]+)"')
_STRING_RE = re.compile(r'^\s+string "([^"]*)"', re.MULTILINE)
_MESSAGE_HEADER_RE = re.compile(
    r"^(?:method call|signal|method return|error)\b"
)
_TOP_LEVEL_INT32_RE = re.compile(r"^ {3}int32[ \t]+[-+]?\d+[ \t]*$")
_RETRY_INITIAL_MS = 1000
_RETRY_MAX_MS = 30000
_MONITOR_STABLE_SECONDS = 5


class NotifyDaemon:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.monitor = None
        self.seen = set(config.load_state().get("apps_seen", []))
        self.lock = threading.Lock()
        self.monitor_lock = threading.Lock()
        self.stopping = False
        self.accept_restarts = False
        self.restart_pending = False
        self.retry_delay_ms = _RETRY_INITIAL_MS
        self.monitor_started_at = None

    def _start_monitor(self):
        with self.monitor_lock:
            self.restart_pending = False
        if self.stopping:
            return False
        self._close_monitor(self.monitor)
        self.monitor = None
        try:
            monitor = subprocess.Popen(
                ["dbus-monitor", MONITOR_RULE],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            sys.stderr.write(
                "NotifySound: no se pudo iniciar dbus-monitor: "
                f"{exc}\n"
            )
            self._schedule_monitor_restart()
            return False
        self.monitor = monitor
        self.monitor_started_at = time.monotonic()
        threading.Thread(
            target=self._reader, args=(monitor,), daemon=True
        ).start()
        return False

    def _close_monitor(self, monitor):
        if monitor is None:
            return
        try:
            if monitor.poll() is None:
                monitor.terminate()
            monitor.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                monitor.kill()
            except (AttributeError, OSError):
                pass
            try:
                monitor.wait(timeout=1)
            except (AttributeError, OSError, subprocess.TimeoutExpired):
                pass
        except (AttributeError, OSError):
            pass
        try:
            if monitor.stdout:
                monitor.stdout.close()
        except (AttributeError, OSError, ValueError):
            pass

    def _schedule_monitor_restart(self):
        if self.stopping or not self.accept_restarts:
            return
        with self.monitor_lock:
            if self.restart_pending:
                return
            delay = self.retry_delay_ms
            self.retry_delay_ms = min(delay * 2, _RETRY_MAX_MS)
            self.restart_pending = True
        try:
            GLib.timeout_add(delay, self._start_monitor)
        except Exception as exc:
            with self.monitor_lock:
                self.restart_pending = False
            sys.stderr.write(
                "NotifySound: no se pudo programar el reinicio del monitor: "
                f"{exc}\n"
            )

    def _process_block(self, lines):
        try:
            self._handle_block(lines)
        except Exception as exc:
            sys.stderr.write(
                "NotifySound: error procesando una notificación: "
                f"{exc}\n"
            )

    def _reader(self, monitor):
        buffer = None
        try:
            for raw in monitor.stdout:
                text = raw.decode(errors="replace")
                is_notify = (
                    text.startswith("method call") and "member=Notify" in text
                )
                if _MESSAGE_HEADER_RE.match(text):
                    if buffer is not None:
                        self._process_block(buffer)
                    buffer = [text] if is_notify else None
                    continue
                if buffer is not None:
                    if _TOP_LEVEL_INT32_RE.match(text):
                        self._process_block(buffer)
                        buffer = None
                    else:
                        buffer.append(text)
            if buffer is not None:
                self._process_block(buffer)
        except (OSError, ValueError) as exc:
            if not self.stopping:
                sys.stderr.write(
                    "NotifySound: se cerró el monitor de notificaciones: "
                    f"{exc}\n"
                )
        finally:
            if self.monitor is monitor:
                self.monitor = None
            if (
                self.monitor_started_at is not None
                and time.monotonic() - self.monitor_started_at
                >= _MONITOR_STABLE_SECONDS
            ):
                self.retry_delay_ms = _RETRY_INITIAL_MS
            if not self.stopping:
                self._schedule_monitor_restart()

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
        if "suppress-sound" in hints:
            return
        cfg = config.load_config()
        if not cfg.get("enabled", True):
            return
        apps = cfg.get("apps", {})
        if not isinstance(apps, dict):
            apps = {}
        app_cfg = apps.get(app_name, {})
        if not isinstance(app_cfg, dict):
            app_cfg = {}
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
        self.stopping = True
        self.accept_restarts = False
        if self.loop.is_running():
            self.loop.quit()
        self._close_monitor(self.monitor)

    def run(self):
        self.accept_restarts = True
        self._start_monitor()
        try:
            self.loop.run()
        finally:
            self.stopping = True
            self.accept_restarts = False
            self._close_monitor(self.monitor)
            self.monitor = None


def main_daemon():
    lock = config.acquire_instance_lock()
    if lock is None:
        print("NotifySound: el daemon ya está corriendo.")
        return 1
    pid_written = False
    try:
        if config.is_running():
            print("NotifySound: el daemon ya está corriendo.")
            return 1
        config.write_pid(lock, os.getpid())
        pid_written = True
        daemon = NotifyDaemon()
        signal.signal(signal.SIGTERM, daemon.on_signal)
        signal.signal(signal.SIGINT, daemon.on_signal)
        daemon.run()
    finally:
        lock.close()
        if pid_written:
            config.remove_pid(os.getpid())
    return 0


def quit_daemon():
    pid = config.read_pid()
    if pid is None or not config.is_running():
        print("NotifySound: el daemon no está corriendo.")
        config.remove_pid()
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        config.remove_pid()
        print("NotifySound: el daemon no está corriendo.")
        return 1
    except PermissionError:
        print("NotifySound: no se puede detener el daemon.")
        return 1
    print("NotifySound: daemon detenido.")
    return 0

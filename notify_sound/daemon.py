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
GTK_MONITOR_RULE = (
    "eavesdrop=true,type='method_call',"
    "interface='org.gtk.Notifications',"
    "member='AddNotification'"
)

_MESSAGE_HEADER_RE = re.compile(
    r"^(?:method call|signal|method return|error)\b"
)
_TOP_LEVEL_INT32_RE = re.compile(r"^ {3}int32[ \t]+[-+]?\d+[ \t]*$")
_RETRY_INITIAL_MS = 1000
_RETRY_MAX_MS = 30000
_MONITOR_STABLE_SECONDS = 5
_MAX_BLOCK_BYTES = 1024 * 1024
_MAX_BLOCK_LINES = 4096


def _scan_line(text, in_string=False, escaped=False):
    """Track dbus-monitor syntax without treating string content as framing."""
    bracket_delta = 0
    array_start = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif text.startswith("array [", index):
                array_start = True
            elif char == "[":
                bracket_delta += 1
            elif char == "]":
                bracket_delta -= 1
        index += 1
    return in_string, escaped, bracket_delta, array_start


def _dbus_tokens(lines):
    """Yield structural dict markers and decoded dbus-monitor strings."""
    in_string = False
    escaped = False
    value = []
    index = 0
    while index < len(lines):
        line = lines[index]
        position = 0
        while position < len(line):
            if in_string:
                char = line[position]
                if escaped:
                    value.append(
                        {
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                            "\\": "\\",
                            '"': '"',
                        }.get(char, char)
                    )
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    yield "string", "".join(value)
                    value = []
                    in_string = False
                else:
                    value.append(char)
                position += 1
                continue
            if line.startswith("dict entry(", position):
                yield "dict", None
                position += len("dict entry(")
            elif line.startswith('string "', position):
                in_string = True
                position += len('string "')
            else:
                position += 1
        index += 1


def _parse_block(lines):
    app_name = None
    hints = set()
    expecting_hint = False
    for token_type, value in _dbus_tokens(lines):
        if token_type == "dict":
            expecting_hint = True
        elif token_type == "string":
            if app_name is None:
                app_name = value
            elif expecting_hint:
                hints.add(value)
                expecting_hint = False
    return app_name, hints


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
                ["dbus-monitor", MONITOR_RULE, GTK_MONITOR_RULE],
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
        message_kind = None
        in_string = False
        escaped = False
        array_depth = 0
        array_started = False
        block_bytes = 0
        block_lines = 0
        oversized = False
        try:
            for raw in monitor.stdout:
                text = raw.decode(errors="replace")
                if not in_string and _MESSAGE_HEADER_RE.match(text):
                    buffer = None
                    message_kind = None
                    array_depth = 0
                    array_started = False
                    block_bytes = 0
                    block_lines = 0
                    oversized = False
                    if text.startswith("method call") and "member=Notify" in text:
                        message_kind = "notify"
                    elif (
                        text.startswith("method call")
                        and "member=AddNotification" in text
                    ):
                        message_kind = "gtk"
                    if message_kind is not None:
                        buffer = [text]
                        block_bytes = len(raw)
                        block_lines = 1
                    continue
                if buffer is not None:
                    if message_kind == "notify" and not in_string and _TOP_LEVEL_INT32_RE.match(
                        text
                    ):
                        if not oversized:
                            self._process_block(buffer)
                        buffer = None
                        message_kind = None
                        continue
                    new_in_string, new_escaped, bracket_delta, starts_array = (
                        _scan_line(text, in_string, escaped)
                    )
                    if message_kind == "gtk":
                        array_started = array_started or starts_array
                        array_depth += bracket_delta
                    in_string = new_in_string
                    escaped = new_escaped
                    block_bytes += len(raw)
                    block_lines += 1
                    if (
                        block_bytes > _MAX_BLOCK_BYTES
                        or block_lines > _MAX_BLOCK_LINES
                    ):
                        oversized = True
                    elif not oversized:
                        buffer.append(text)
                    if (
                        message_kind == "gtk"
                        and array_started
                        and array_depth <= 0
                        and not in_string
                    ):
                        if not oversized:
                            self._process_block(buffer)
                        buffer = None
                        message_kind = None
                        array_depth = 0
                        array_started = False
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
        app_name, hints = _parse_block(lines)
        if not app_name or len(app_name) > config.MAX_APP_NAME_LENGTH:
            return
        if "x-shell-sender" in hints:
            return
        self._record_app(app_name)
        self._maybe_play(app_name, hints)

    def _record_app(self, app_name):
        if not app_name:
            return
        with self.lock:
            if app_name not in self.seen:
                if len(self.seen) >= config.MAX_STATE_APPS:
                    return
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
    deadline = time.monotonic() + 3
    while config.is_running() and time.monotonic() < deadline:
        time.sleep(0.05)
    if config.is_running():
        print("NotifySound: el daemon no se detuvo a tiempo.")
        return 1
    print("NotifySound: daemon detenido.")
    return 0

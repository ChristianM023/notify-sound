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
_SENDER_RE = re.compile(r"\bsender=(:[0-9]+\.[0-9]+)")
_SENDER_VALUE_RE = re.compile(r"^:[0-9]+\.[0-9]+$")
_DBUS_PID_RE = re.compile(r"^\s*uint32\s+(\d+)\s*$")
_RETRY_INITIAL_MS = 1000
_RETRY_MAX_MS = 30000
_MONITOR_STABLE_SECONDS = 5
_MAX_BLOCK_BYTES = 1024 * 1024
_MAX_BLOCK_LINES = 4096
_SENDER_CACHE_TTL_SECONDS = 300
_GENERIC_COMMS = {
    "python3", "python", "python2", "sh", "bash", "dash", "csh", "zsh",
    "dbus-monitor", "dbus-daemon",
}

_sender_cache = {}


def _parse_sender(lines):
    if not lines:
        return None
    match = _SENDER_RE.search(lines[0])
    return match.group(1) if match else None


def _query_connection_pid(sender):
    try:
        result = subprocess.run(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus.GetConnectionUnixProcessID",
                f"string:{sender}",
            ],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    for line in result.stdout.splitlines():
        match = _DBUS_PID_RE.match(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _read_proc_comm(pid):
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as handle:
            return handle.read().strip() or None
    except (OSError, ValueError):
        return None


def _read_proc_cmdline_name(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            parts = handle.read().split(b"\0")
    except (OSError, ValueError):
        return None
    for part in parts:
        if not part:
            continue
        try:
            name = os.path.basename(part.decode(errors="replace"))
        except (OSError, ValueError):
            continue
        if name and name not in _GENERIC_COMMS and not name.startswith("python"):
            base = os.path.splitext(name)[0]
            if base and 0 < len(base) <= config.MAX_APP_NAME_LENGTH:
                return base
    return None


def _resolve_sender_to_comm(sender):
    if not sender or not _SENDER_VALUE_RE.fullmatch(sender):
        return None
    now = time.monotonic()
    cached = _sender_cache.get(sender)
    if cached and now - cached[0] < _SENDER_CACHE_TTL_SECONDS:
        return cached[1]
    pid = _query_connection_pid(sender)
    comm = _read_proc_comm(pid) if pid else None
    # /proc/PID/comm is kernel-truncated to 15 chars (TASK_COMM_LEN).
    # If it's exactly 15, assume truncation and try cmdline for the
    # full binary name (e.g. "telegram-deskto" -> "telegram-desktop").
    if (not comm) or (comm in _GENERIC_COMMS) or (len(comm) == 15):
        cmdline_name = _read_proc_cmdline_name(pid) if pid else None
        if cmdline_name:
            comm = cmdline_name
    if comm and (
        not isinstance(comm, str)
        or not (0 < len(comm) <= config.MAX_APP_NAME_LENGTH)
        or comm in _GENERIC_COMMS
    ):
        comm = None
    _sender_cache[sender] = (now, comm)
    return comm


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
    """Yield structural dict markers and decoded dbus-monitor strings.

    Tokens emitted:
      ("dict", None)                 -- start of a dict entry (a hint pair)
      ("string", value)              -- a bare ``string "..."`` argument
      ("variant_string", value)       -- a ``variant string "..."`` argument
      ("variant_array_string", value) -- a ``variant array string "..."`` item

    The variant tokens let callers read hint *values* (e.g. the
    ``desktop-entry`` hint) without re-introducing column/blank-line based
    framing: quoted content is still tracked with the same state machine.
    """
    in_string = False
    escaped = False
    value = []
    _pending_string_kind = None
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
                    yield _pending_string_kind, "".join(value)
                    value = []
                    in_string = False
                else:
                    value.append(char)
                position += 1
                continue
            if line.startswith("dict entry(", position):
                yield "dict", None
                position += len("dict entry(")
            elif line.startswith('variant array string "', position):
                in_string = True
                _pending_string_kind = "variant_array_string"
                position += len('variant array string "')
            elif line.startswith('variant string "', position):
                in_string = True
                _pending_string_kind = "variant_string"
                position += len('variant string "')
            elif line.startswith('string "', position):
                in_string = True
                _pending_string_kind = "string"
                position += len('string "')
            else:
                position += 1
        index += 1


def _parse_block(lines):
    app_name = None
    hints = set()
    hint_key = None
    desktop_entry = None
    expecting_hint = False
    for token_type, value in _dbus_tokens(lines):
        if token_type == "dict":
            expecting_hint = True
            hint_key = None
        elif token_type == "string":
            if app_name is None:
                app_name = value
            elif expecting_hint and hint_key is None:
                hint_key = value
                hints.add(value)
        elif token_type == "variant_string" and expecting_hint and hint_key == "desktop-entry":
            if desktop_entry is None:
                desktop_entry = value
            expecting_hint = False
    return app_name, hints, desktop_entry


class NotifyDaemon:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.monitor = None
        initial_state = config.load_state()
        self.seen = set(initial_state.get("apps_seen", []))
        self._meta_cache = dict(initial_state.get("app_meta", {}))
        self.lock = threading.Lock()
        self.monitor_lock = threading.Lock()
        self.stopping = False
        self.accept_restarts = False
        self.restart_pending = False
        self.retry_delay_ms = _RETRY_INITIAL_MS
        self.monitor_started_at = None
        self._state_mtime = 0.0
        self._sync_seen_with_state()

    def _sync_seen_with_state(self):
        """Reconcile the in-memory ``seen`` set with persisted state.

        Cheap: only touches disk when ``state.json`` mtime changed since
        the last call. After the GUI clears/resets the list, this drops
        from ``self.seen`` anything no longer in ``apps_seen`` so the
        next notification for that app is treated as new again.
        """
        try:
            mtime = os.path.getmtime(config.STATE_FILE)
        except OSError:
            return
        if mtime == self._state_mtime:
            return
        self._state_mtime = mtime
        try:
            state = config.load_state()
        except (OSError, ValueError):
            return
        persisted = set(state.get("apps_seen", []))
        with self.lock:
            self.seen &= persisted
            self._meta_cache = dict(state.get("app_meta", {}))

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
        app_name, hints, desktop_entry = _parse_block(lines)
        if "x-shell-sender" in hints:
            return
        cfg = config.load_config()
        canonical = None
        comm = None
        if (
            desktop_entry
            and isinstance(desktop_entry, str)
            and 0 < len(desktop_entry) <= config.MAX_APP_NAME_LENGTH
        ):
            canonical = desktop_entry
        else:
            sender = _parse_sender(lines)
            comm = _resolve_sender_to_comm(sender) if sender else None
            if (
                comm
                and isinstance(comm, str)
                and 0 < len(comm) <= config.MAX_APP_NAME_LENGTH
            ):
                canonical = comm
        if not canonical and app_name:
            synonym_owner = self._find_synonym_owner(cfg, app_name)
            if synonym_owner and isinstance(synonym_owner, str):
                canonical = synonym_owner
            elif len(app_name) <= config.MAX_APP_NAME_LENGTH:
                canonical = app_name
        if not canonical:
            return
        self._record_app(canonical, comm)
        self._maybe_play(canonical, hints, cfg)

    @staticmethod
    def _find_synonym_owner(cfg, app_name):
        apps = cfg.get("apps", {}) if isinstance(cfg, dict) else {}
        if not isinstance(apps, dict):
            return None
        for owner, app_cfg in apps.items():
            if not isinstance(app_cfg, dict):
                continue
            if app_name in (app_cfg.get("synonyms") or []):
                return owner
        return None

    def _record_app(self, app_name, comm=None):
        if not app_name:
            return
        self._sync_seen_with_state()
        with self.lock:
            is_new = app_name not in self.seen
            if is_new and len(self.seen) >= config.MAX_STATE_APPS:
                return
            if is_new:
                self.seen.add(app_name)
            cached = dict(self._meta_cache.get(app_name, {}))
            cached["seen_count"] = int(cached.get("seen_count", 0)) + 1
            cached["last_seen"] = time.time()
            if comm:
                cached["comm"] = comm
            self._meta_cache[app_name] = cached
            state = config.load_state()
            apps_seen = list(state.get("apps_seen", []))
            if is_new and app_name not in apps_seen:
                apps_seen.append(app_name)
            app_meta = dict(state.get("app_meta", {}))
            app_meta[app_name] = cached
            config.save_state(
                {"apps_seen": apps_seen, "app_meta": app_meta}
            )

    def _maybe_play(self, app_name, hints, cfg=None):
        if "suppress-sound" in hints:
            return
        if cfg is None:
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
        if pid_written:
            config.remove_pid(os.getpid())
        lock.close()
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

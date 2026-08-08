import fcntl
import json
import os
import shutil
import tempfile

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "notify-sound",
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
_runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "notify-sound",
)
PID_FILE = os.path.join(_runtime_dir, "notify-sound.pid")
AUTOSTART_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "autostart",
)
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "notify-sound.desktop")

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
MAX_CONFIG_BYTES = 1024 * 1024
MAX_STATE_BYTES = 256 * 1024
MAX_STATE_APPS = 512
MAX_APP_NAME_LENGTH = 256
MAX_PATH_LENGTH = 4096

AUTOSTART_DESKTOP = """[Desktop Entry]
Type=Application
Name=NotifySound daemon
Comment=Play sounds for notifications that do not include one
Exec={bin} --daemon
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
"""

DEFAULT_CONFIG = {
    "enabled": True,
    "sound": "message",
    "custom_sounds": [],
    "no_duplicate": True,
    "autostart": True,
    "apps": {},
}


def autostart_enabled():
    return os.path.exists(AUTOSTART_FILE)


def _ensure_private_directory(path):
    if os.path.islink(path):
        raise OSError(f"directorio inseguro: {path}")
    os.makedirs(path, mode=PRIVATE_DIR_MODE, exist_ok=True)
    os.chmod(path, PRIVATE_DIR_MODE)


def _desktop_exec(path):
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError("la ruta del ejecutable debe ser absoluta")
    if any(
        ord(char) < 0x20 or char in '"\r\n%' for char in path
    ):
        raise ValueError("la ruta del ejecutable contiene caracteres invalidos")
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _notify_binary():
    configured = os.environ.get("NOTIFY_SOUND_BIN")
    if configured and os.path.isabs(configured):
        return configured
    found = shutil.which("notify-sound")
    if found and os.path.isabs(found):
        return found
    return os.path.expanduser(
        "~/.local/bin/notify-sound"
    )


def set_autostart(enabled):
    if enabled:
        _ensure_private_directory(AUTOSTART_DIR)
        _atomic_write(
            AUTOSTART_FILE,
            AUTOSTART_DESKTOP.format(bin=_desktop_exec(_notify_binary())),
        )
    else:
        try:
            os.unlink(AUTOSTART_FILE)
        except FileNotFoundError:
            pass


def _normalize_app(app):
    normalized = dict(app) if isinstance(app, dict) else {}
    enabled = normalized.get("enabled", True)
    sound = normalized.get("sound")
    normalized["enabled"] = enabled if isinstance(enabled, bool) else True
    normalized["sound"] = (
        sound
        if sound is None
        or (isinstance(sound, str) and 0 < len(sound) <= MAX_PATH_LENGTH)
        else None
    )
    return normalized


def load_config():
    cfg = {
        "enabled": DEFAULT_CONFIG["enabled"],
        "sound": DEFAULT_CONFIG["sound"],
        "custom_sounds": list(DEFAULT_CONFIG["custom_sounds"]),
        "no_duplicate": DEFAULT_CONFIG["no_duplicate"],
        "autostart": DEFAULT_CONFIG["autostart"],
        "apps": {},
    }
    data = {}
    try:
        if os.path.getsize(CONFIG_FILE) > MAX_CONFIG_BYTES:
            return cfg
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        for key, value in data.items():
            if key == "apps" and isinstance(value, dict):
                cfg["apps"] = {
                    name: _normalize_app(app)
                    for name, app in value.items()
                    if isinstance(name, str)
                    and 0 < len(name) <= MAX_APP_NAME_LENGTH
                }
            elif key == "custom_sounds" and isinstance(value, list):
                cfg["custom_sounds"] = [
                    p
                    for p in value
                    if isinstance(p, str) and 0 < len(p) <= MAX_PATH_LENGTH
                ]
            elif key in ("enabled", "no_duplicate", "autostart") and isinstance(
                value, bool
            ):
                cfg[key] = value
            elif (
                key == "sound"
                and isinstance(value, str)
                and 0 < len(value) <= MAX_PATH_LENGTH
            ):
                cfg[key] = value
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ):
        pass
    legacy = data.get("custom_sound")
    if legacy and isinstance(legacy, str) and legacy not in cfg["custom_sounds"]:
        cfg["custom_sounds"].append(legacy)
        cfg["sound"] = legacy
        try:
            save_config(cfg)
        except OSError:
            pass
    return cfg


def _atomic_write(path, contents):
    directory = os.path.dirname(path) or "."
    _ensure_private_directory(directory)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", dir=directory
    )
    open_fd = fd
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            open_fd = None
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, PRIVATE_FILE_MODE)
        directory_fd = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if open_fd is not None:
            os.close(open_fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def save_config(cfg):
    contents = json.dumps(cfg, indent=2)
    if len(contents.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ValueError("la configuracion es demasiado grande")
    _atomic_write(
        CONFIG_FILE,
        contents,
    )


def load_state():
    try:
        if os.path.getsize(STATE_FILE) > MAX_STATE_BYTES:
            return {"apps_seen": []}
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("apps_seen"), list):
            apps_seen = []
            seen = set()
            for app_name in data["apps_seen"]:
                if (
                    isinstance(app_name, str)
                    and 0 < len(app_name) <= MAX_APP_NAME_LENGTH
                    and app_name not in seen
                ):
                    seen.add(app_name)
                    apps_seen.append(app_name)
                if len(apps_seen) >= MAX_STATE_APPS:
                    break
            return {
                "apps_seen": apps_seen
            }
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ):
        pass
    return {"apps_seen": []}


def save_state(state):
    apps_seen = []
    seen = set()
    for app_name in state.get("apps_seen", []) if isinstance(state, dict) else []:
        if (
            isinstance(app_name, str)
            and 0 < len(app_name) <= MAX_APP_NAME_LENGTH
            and app_name not in seen
        ):
            seen.add(app_name)
            apps_seen.append(app_name)
        if len(apps_seen) >= MAX_STATE_APPS:
            break
    _atomic_write(STATE_FILE, json.dumps({"apps_seen": apps_seen}))


def is_running():
    pid = read_pid()
    if pid is None:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            parts = f.read().split(b"\0")
        return (
            b"--daemon" in parts
            and any(
                part == b"notify-sound" or part.endswith(b"/notify-sound")
                for part in parts
            )
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False


def read_pid():
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(PID_FILE, flags)
        with os.fdopen(fd, encoding="utf-8") as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def acquire_instance_lock():
    _ensure_private_directory(os.path.dirname(PID_FILE) or ".")
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    lock_file = PID_FILE + ".lock"
    fd = os.open(lock_file, flags, 0o600)
    handle = None
    try:
        os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "r+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if handle is not None:
            handle.close()
        else:
            os.close(fd)
        return None
    except Exception:
        if handle is not None:
            handle.close()
        else:
            os.close(fd)
        raise
    return handle


def write_pid(handle, pid):
    _atomic_write(PID_FILE, str(pid))


def remove_pid(pid=None):
    if pid is not None and read_pid() != pid:
        return
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass

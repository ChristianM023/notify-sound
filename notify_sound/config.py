import fcntl
import json
import os
import shutil

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

AUTOSTART_DESKTOP = """[Desktop Entry]
Type=Application
Name=NotifySound daemon
Comment=Play sounds for notifications that do not include one
Exec="{bin}" --daemon
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


def _notify_binary():
    configured = os.environ.get("NOTIFY_SOUND_BIN")
    if configured:
        return configured
    return shutil.which("notify-sound") or os.path.expanduser(
        "~/.local/bin/notify-sound"
    )


def set_autostart(enabled):
    if enabled:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        with open(AUTOSTART_FILE, "w", encoding="utf-8") as f:
            f.write(
                AUTOSTART_DESKTOP.format(
                    bin=_notify_binary()
                )
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
        sound if sound is None or isinstance(sound, str) else None
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
                }
            elif key == "custom_sounds" and isinstance(value, list):
                cfg["custom_sounds"] = [
                    p for p in value if isinstance(p, str) and p
                ]
            elif key in ("enabled", "no_duplicate", "autostart") and isinstance(
                value, bool
            ):
                cfg[key] = value
            elif key == "sound" and isinstance(value, str) and value:
                cfg[key] = value
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    legacy = data.get("custom_sound")
    if legacy and isinstance(legacy, str) and legacy not in cfg["custom_sounds"]:
        cfg["custom_sounds"].append(legacy)
        cfg["sound"] = legacy
        save_config(cfg)
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("apps_seen"), list):
            return {
                "apps_seen": [
                    app_name
                    for app_name in data["apps_seen"]
                    if isinstance(app_name, str)
                ]
            }
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {"apps_seen": []}


def save_state(state):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def is_running():
    pid = read_pid()
    if pid is None:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().decode(errors="replace")
        return "notify-sound" in cmd and "--daemon" in cmd
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False


def read_pid():
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def acquire_instance_lock():
    os.makedirs(os.path.dirname(PID_FILE) or ".", exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(PID_FILE, flags, 0o600)
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
    handle.seek(0)
    handle.truncate()
    handle.write(str(pid))
    handle.flush()
    os.fsync(handle.fileno())


def remove_pid(pid=None):
    if pid is not None and read_pid() != pid:
        return
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass

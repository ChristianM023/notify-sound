import os
import subprocess
import threading
from pathlib import Path

_CANBERRA_EXTENSIONS = (".ogg", ".oga", ".wav", ".flac")


def _run(command):
    try:
        return subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None


def play_sound(sound_id):
    return _run(["canberra-gtk-play", "-i", sound_id]) is not None


def _play_fallback(commands):
    for command in commands:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            continue
        try:
            if process.wait() == 0:
                return True
        except OSError:
            continue
    return False


def play_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _CANBERRA_EXTENSIONS:
        return _run(["canberra-gtk-play", "--file", path]) is not None
    try:
        uri = Path(path).expanduser().resolve().as_uri()
    except (OSError, ValueError):
        return False
    commands = (
        ["gst-launch-1.0", "playbin", f"uri={uri}"],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["mpv", "--no-terminal", "--really-quiet", path],
        ["mpg123", "-q", path],
    )
    threading.Thread(
        target=_play_fallback, args=(commands,), daemon=True
    ).start()
    return True


def play_choice(choice):
    if not isinstance(choice, str) or not choice:
        return False
    if os.path.exists(choice):
        return play_file(choice)
    return play_sound(choice)

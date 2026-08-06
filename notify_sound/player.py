import os
import subprocess
from pathlib import Path

_CANBERRA_EXTENSIONS = (".ogg", ".oga", ".wav", ".flac")


def _run(command):
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def play_sound(sound_id):
    _run(["canberra-gtk-play", "-i", sound_id])


def play_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _CANBERRA_EXTENSIONS:
        _run(["canberra-gtk-play", "--file", path])
        return
    uri = Path(path).as_uri()
    for command in (
        ["gst-launch-1.0", "playbin", f"uri={uri}"],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["mpv", "--no-terminal", "--really-quiet", path],
        ["mpg123", "-q", path],
    ):
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except FileNotFoundError:
            continue


def play_choice(choice):
    if not choice:
        return
    if os.path.exists(choice):
        play_file(choice)
    else:
        play_sound(choice)

import glob
import os
import subprocess

AUDIO_EXTENSIONS = ("*.oga", "*.ogg", "*.wav")


def theme_name():
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.sound", "theme-name"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        name = out.strip("'\"").strip()
        return name or "freedesktop"
    except (FileNotFoundError, subprocess.SubprocessError):
        return "freedesktop"


def _sound_bases():
    data_dirs = os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
    ).split(":")
    bases = [os.path.join(d, "sounds") for d in data_dirs if d]
    bases += [os.path.expanduser("~/.local/share/sounds")]
    return bases


def list_sounds():
    sounds = {}
    themes = [theme_name(), "freedesktop"]
    for base in _sound_bases():
        for theme in themes:
            stereo = os.path.join(base, theme, "stereo")
            for pattern in AUDIO_EXTENSIONS:
                for path in sorted(glob.glob(os.path.join(stereo, pattern))):
                    sound_id = os.path.splitext(os.path.basename(path))[0]
                    sounds.setdefault(sound_id, path)
    return sounds


def default_sound_id():
    sounds = list_sounds()
    if "message" in sounds:
        return "message"
    return next(iter(sounds)) if sounds else "message"

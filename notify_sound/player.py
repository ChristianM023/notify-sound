import math
import os
import re
import stat
import subprocess
import threading
from pathlib import Path

_CANBERRA_EXTENSIONS = (".ogg", ".oga", ".wav", ".flac")
_SOUND_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_AUDIO_BYTES = 32 * 1024 * 1024
_PLAYBACK_TIMEOUT_SECONDS = 30
_PLAYBACK_SLOTS = threading.BoundedSemaphore(8)


def _finish_process(process, timeout):
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.terminate()
            process.wait(timeout=1)
        except (AttributeError, OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except (AttributeError, OSError):
                pass
            try:
                process.wait(timeout=1)
            except (AttributeError, OSError, subprocess.TimeoutExpired):
                pass
    except (AttributeError, OSError):
        pass
    finally:
        _PLAYBACK_SLOTS.release()


def _run(command):
    if not _PLAYBACK_SLOTS.acquire(blocking=False):
        return None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        _PLAYBACK_SLOTS.release()
        return None
    threading.Thread(
        target=_finish_process,
        args=(process, _PLAYBACK_TIMEOUT_SECONDS),
        daemon=True,
    ).start()
    return process


def _normalize_volume(volume):
    """Normaliza el volumen a int 0-100; inválido o fuera de rango → 100."""
    if isinstance(volume, bool) or not isinstance(volume, int):
        return 100
    if volume < 0 or volume > 100:
        return 100
    return volume


def _canberra_volume_flag(volume):
    """canberra-gtk-play --volume=<dB>: valor en dB (0.0 = 100 %)."""
    if volume >= 100:
        return []
    db = 20 * math.log10(volume / 100)
    return [f"--volume={db:.2f}"]


def _gst_volume_flag(volume):
    """gst-launch-1.0 playbin volume=<float 0.0-1.0> (1.0 = 100 %)."""
    if volume >= 100:
        return []
    return [f"volume={volume / 100:.2f}"]


def _ffplay_volume_flag(volume):
    """ffplay -volume <0-100> (0 = silencio, 100 = sin reducción)."""
    if volume >= 100:
        return []
    return ["-volume", str(volume)]


def _mpv_volume_flag(volume):
    """mpv --volume=<0-100> (0 = silencio, 100 = sin reducción)."""
    if volume >= 100:
        return []
    return [f"--volume={volume}"]


def _mpg123_volume_flag(volume):
    """mpg123 -f <0-32768> (scale factor, default 32768 = 100 %)."""
    if volume >= 100:
        return []
    return ["-f", str(round(volume / 100 * 32768))]


def play_sound(sound_id, volume=100):
    if not isinstance(sound_id, str) or not _SOUND_ID_RE.fullmatch(sound_id):
        return False
    volume = _normalize_volume(volume)
    if volume == 0:
        return False
    command = ["canberra-gtk-play", "-i", sound_id]
    command.extend(_canberra_volume_flag(volume))
    return _run(command) is not None


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


def _play_fallback_with_limits(commands):
    if not _PLAYBACK_SLOTS.acquire(blocking=False):
        return False
    try:
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
                status = process.wait(timeout=_PLAYBACK_TIMEOUT_SECONDS)
                if status == 0:
                    return True
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except (AttributeError, OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                    except (AttributeError, OSError):
                        pass
                    try:
                        process.wait(timeout=1)
                    except (AttributeError, OSError, subprocess.TimeoutExpired):
                        pass
            except (AttributeError, OSError):
                continue
        return False
    finally:
        _PLAYBACK_SLOTS.release()


def _regular_audio_path(path):
    if not isinstance(path, str) or not path or len(path) > 4096:
        return None
    try:
        candidate = os.path.abspath(os.path.expanduser(path))
        info = os.stat(candidate, follow_symlinks=False)
    except (OSError, ValueError):
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    if info.st_size > _MAX_AUDIO_BYTES:
        return None
    return candidate


def play_file(path, volume=100):
    path = _regular_audio_path(path)
    if path is None:
        return False
    volume = _normalize_volume(volume)
    if volume == 0:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext in _CANBERRA_EXTENSIONS:
        command = ["canberra-gtk-play", "--file", path]
        command.extend(_canberra_volume_flag(volume))
        return _run(command) is not None
    try:
        uri = Path(path).as_uri()
    except (OSError, ValueError):
        return False
    commands = (
        ["gst-launch-1.0", "playbin", f"uri={uri}"]
        + _gst_volume_flag(volume),
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
        + _ffplay_volume_flag(volume)
        + [path],
        ["mpv", "--no-terminal", "--really-quiet"]
        + _mpv_volume_flag(volume)
        + [path],
        ["mpg123", "-q"]
        + _mpg123_volume_flag(volume)
        + [path],
    )
    threading.Thread(
        target=_play_fallback_with_limits, args=(commands,), daemon=True
    ).start()
    return True


def play_choice(choice, volume=100):
    if not isinstance(choice, str) or not choice:
        return False
    if _regular_audio_path(choice) is not None:
        return play_file(choice, volume=volume)
    return play_sound(choice, volume=volume)

"""Envío de notificaciones D-Bus estándar vía dbus-send (subprocess).

Consistente con el patrón del daemon (`dbus-monitor` vía subprocess):
misma invocación con `capture_output=True`, `text=True` y timeout acotado
que `_query_connection_pid` en `daemon.py`. Sin dependencias nuevas
(solo stdlib: `os`, `subprocess`).
"""

import os
import subprocess

DEFAULT_MESSAGE = "Comando finalizado"
_DBUS_SEND_TIMEOUT = 2


def send_done_notification(message=None):
    """Envía una notificación de finalización vía `dbus-send --session`.

    Retorna ``(True, None)`` si el envío fue exitoso, o ``(False, mensaje)``
    con un mensaje de error en español si falló (sin sesión D-Bus,
    dbus-send ausente, timeout o returncode != 0). El entrypoint imprime
    el mensaje y sale con código distinto de 0.
    """
    if message is None or not message.strip():
        message = DEFAULT_MESSAGE
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return (
            False,
            "NotifySound: no hay sesión D-Bus "
            "(DBUS_SESSION_BUS_ADDRESS no está definido).",
        )
    command = [
        "dbus-send", "--session",
        "--dest=org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications.Notify",
        "string:notify-sound",
        "uint32:0",
        "string:",
        "string:NotifySound",
        f"string:{message}",
        "array:string:",
        "dict:string:string:",
        "int32:-1",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_DBUS_SEND_TIMEOUT
        )
    except FileNotFoundError:
        return False, "NotifySound: no se encontró dbus-send."
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"NotifySound: falló el envío de la notificación: {exc}"
    if result.returncode != 0:
        detail = result.stderr.strip() if result.stderr else ""
        suffix = f" ({detail})" if detail else ""
        return (
            False,
            "NotifySound: falló el envío de la notificación "
            f"(dbus-send salió con código {result.returncode}){suffix}.",
        )
    return True, None
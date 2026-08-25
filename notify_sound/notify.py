"""Envío de notificaciones D-Bus estándar vía Gio.DBus.

Gio.DBus (python3-gi) es dependencia del proyecto: el daemon ya importa
`gi.repository.GLib` para el MainLoop. Se usa en lugar de `dbus-send`
(subprocess) porque `dbus-send` no soporta variants: los hints de
`org.freedesktop.Notifications.Notify` son `dict<string,variant>` (`a{sv}`)
y `dbus-send` solo puede enviar `dict:string:string:` (`a{ss}`), que el
servidor de notificaciones rechaza con InvalidArgs. Además, `dbus-send` sin
`--print-reply` retorna exit code 0 aunque el servidor rechace la
notificación, ocultando el fallo.
"""

import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

DEFAULT_MESSAGE = "Comando finalizado"
_DBUS_CALL_TIMEOUT_MS = 2000


def send_done_notification(message=None):
    """Envía una notificación de finalización vía Gio.DBus (bus de sesión).

    Retorna ``(True, None)`` si el envío fue exitoso, o ``(False, mensaje)``
    con un mensaje de error en español si falló (sin sesión D-Bus, fallo de
    conexión al bus o error de la llamada). El entrypoint imprime el mensaje
    y sale con código distinto de 0.
    """
    if message is None or not message.strip():
        message = DEFAULT_MESSAGE
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return (
            False,
            "NotifySound: no hay sesión D-Bus "
            "(DBUS_SESSION_BUS_ADDRESS no está definido).",
        )
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return False, "NotifySound: no se pudo conectar al bus de sesión D-Bus."
    params = GLib.Variant(
        "(susssasa{sv}i)",
        ("notify-sound", 0, "", "NotifySound", message, [], {}, -1),
    )
    try:
        bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            params,
            None,
            Gio.DBusCallFlags.NONE,
            _DBUS_CALL_TIMEOUT_MS,
            None,
        )
    except GLib.Error as exc:
        return False, f"NotifySound: falló el envío de la notificación: {exc}"
    return True, None

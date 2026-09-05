"""Envío de notificaciones D-Bus estándar vía Gio.DBus.

Gio.DBus (python3-gi) es dependencia del proyecto: el daemon ya importa
`gi.repository.GLib` para el MainLoop. Se usa en lugar de `dbus-send`
(subprocess) porque `dbus-send` no soporta variants: los hints de
`org.freedesktop.Notifications.Notify` son `dict<string,variant>` (`a{sv}`)
y `dbus-send` solo puede enviar `dict:string:string:` (`a{ss}`), que el
servidor de notificaciones rechaza con InvalidArgs. Además, `dbus-send` sin
`--print-reply` retorna exit code 0 aunque el servidor rechace la
notificación, ocultando el fallo.

El app_name enviado es `notify-send` (no `notify-sound`): verificado
empíricamente en GNOME, gnome-shell descarta las notificaciones con
app_name `notify-sound` (no muestra el banner) mientras que `notify-send`
sí se muestra. El marcador de notificación propia viaja en el hint
`x-notify-sound-done`, que el daemon usa para reconocerla sin depender del
app_name.
"""

import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

DEFAULT_MESSAGE = "Comando finalizado"
_DBUS_CALL_TIMEOUT_MS = 2000


def _build_notify_params(message):
    """Construye el Variant `(susssasa{sv}i)` de Notify con VariantBuilder.

    El constructor directo de GLib.Variant no maneja `a{sv}` con dicts de
    Python (verificado empíricamente); VariantBuilder construye los hints
    como entradas `{sv}` explícitas. El app_name es `notify-send` para que
    gnome-shell muestre el banner; el hint `x-notify-sound-done` marca la
    notificación como propia para el daemon.
    """
    builder = GLib.VariantBuilder(GLib.VariantType("(susssasa{sv}i)"))
    builder.add_value(GLib.Variant("s", "notify-send"))  # app_name
    builder.add_value(GLib.Variant("u", 0))               # replaces_id
    builder.add_value(GLib.Variant("s", ""))              # app_icon
    builder.add_value(GLib.Variant("s", "NotifySound"))   # summary
    builder.add_value(GLib.Variant("s", message))         # body
    builder.add_value(
        GLib.VariantBuilder(GLib.VariantType("as")).end()  # actions vacío
    )
    hints_builder = GLib.VariantBuilder(GLib.VariantType("a{sv}"))
    hints_builder.add_value(
        GLib.Variant("{sv}", ("urgency", GLib.Variant("y", 1)))
    )
    hints_builder.add_value(
        GLib.Variant("{sv}", ("x-notify-sound-done", GLib.Variant("s", "1")))
    )
    builder.add_value(hints_builder.end())  # hints
    builder.add_value(GLib.Variant("i", -1))  # expire_timeout
    return builder.end()


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
    params = _build_notify_params(message)
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

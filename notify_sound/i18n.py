"""Catálogo de traducciones ES/EN para la GUI (ADR 0013).

Mecanismo simple en Python: dos dicts con paridad exacta de claves y una
función de traducción con interpolación. Sin gettext ni archivos .po/.mo.
El idioma activo es estado de módulo, configurable en caliente desde la
GUI (el selector de idioma de la ventana lo cambia sin reiniciar).
"""

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "es")

EN = {
    "master_label": "Notification sound",
    "autostart_label": "Start with session",
    "sound_label": "Global sound:",
    "test_button": "Test",
    "add_custom_button": "Add custom sound...",
    "formats_hint": (
        "Formats: OGG, WAV and FLAC. MP3/M4A/AAC need gst-launch-1.0, "
        "ffplay, mpv or mpg123."
    ),
    "debounce_label": "Anti-burst (s):",
    "debounce_tooltip": (
        "Seconds between sounds from the same app. 0 disables. Within the "
        "window, only the first notification of a burst plays."
    ),
    "apps_header": "Applications",
    "reset_apps_button": "Clear list",
    "reset_apps_tooltip": "Remove all detected apps and their configuration",
    "sort_label": "Order:",
    "sort_arrival": "By arrival",
    "sort_name": "By name",
    "sort_count": "By notifications",
    "start_daemon": "Start daemon",
    "stop_daemon": "Stop daemon",
    "daemon_running": "Daemon: running",
    "daemon_stopped": "Daemon: stopped",
    "inherited": "Inherited",
    "own_sound_tooltip": (
        "This app already plays its own sound. NotifySound does not "
        "duplicate it by default (the switch is off). If you enable the "
        "switch, both sounds will play."
    ),
    "own_sound_label": "Has its own sound",
    "volume_tooltip": "Volume for this application (0-100 %)",
    "test_tooltip": "Test",
    "rename_tooltip": "Rename",
    "info_tooltip": "Information",
    "rules_tooltip": "Edit sound rules by content for this application",
    "remove_tooltip": "Remove from list",
    "save_button": "Save",
    "reset_button": "Reset",
    "merge_confirm": "Merge",
    "cancel_button": "Cancel",
    "merge_message": (
        "The alias «{alias}» is already used by «{owner_display}».\n"
        "Merge? «{source_display}» will be removed, its future "
        "notifications will be attributed to «{owner_display}» and "
        "«{source}» will be added as a synonym."
    ),
    "rules_dialog_title": "Rules for {name}",
    "add_rule_button": "Add rule",
    "close_button": "Close",
    "drag_handle_tooltip": "Drag to reorder",
    "rule_field_label": "Field",
    "rule_op_label": "Operator",
    "rule_value_label": "Value",
    "rule_action_label": "Action",
    "rule_sound_label": "Sound",
    "delete_rule_tooltip": "Delete rule",
    "rule_field_body": "Body",
    "rule_field_summary": "Title",
    "rule_field_urgency": "Urgency",
    "rule_op_contains": "Contains",
    "rule_op_regex": "Regex",
    "rule_op_eq": "Equals",
    "rule_op_starts_with": "Starts with",
    "rule_op_ends_with": "Ends with",
    "rule_action_sound": "Play",
    "rule_action_silence": "Silence",
    "urgency_low": "Low",
    "urgency_normal": "Normal",
    "urgency_critical": "Critical",
    "synonyms_header": "Synonyms (press Restore to split):",
    "restore_button": "Restore",
    "reset_confirm_message": (
        "Clear the list of detected applications and their per-app "
        "configuration? All renames, synonyms and counters will be "
        "deleted. Apps will be detected again when they receive "
        "notifications."
    ),
    "reset_confirm_button": "Clear list",
    "info_notification_name": "Notification name: {app_name}",
    "info_displayed_as": "Displayed as: {display}",
    "info_sender_process": "Sender process: {comm}",
    "info_own_sound": "Own sound: {value}",
    "info_yes": "yes",
    "info_no": "no",
    "info_synonym_count": "Number of synonyms: {count}",
    "info_notification_count": "Notifications: {count}",
    "info_last_seen": "Last seen: {stamp}",
    "info_legacy_note": (
        "This application was detected before v0.1.9; no notification "
        "from it has been observed with the current daemon yet. When one "
        "arrives, process, counter and last seen will be filled in."
    ),
    "remove_custom_button": "Remove",
    "choose_sound_file": "Choose sound file",
    "audio_filter": "Audio",
    "language_label": "Language:",
}

ES = {
    "master_label": "Sonido de notificaciones",
    "autostart_label": "Iniciar con la sesión",
    "sound_label": "Sonido global:",
    "test_button": "Probar",
    "add_custom_button": "Añadir sonido propio...",
    "formats_hint": (
        "Formatos: OGG, WAV y FLAC. MP3/M4A/AAC necesitan gst-launch-1.0, "
        "ffplay, mpv o mpg123."
    ),
    "debounce_label": "Anti-ráfaga (s):",
    "debounce_tooltip": (
        "Segundos entre sonidos de una misma app. 0 desactiva. Dentro de "
        "la ventana, solo suena la primera notificación de una ráfaga."
    ),
    "apps_header": "Aplicaciones",
    "reset_apps_button": "Vaciar lista",
    "reset_apps_tooltip": "Borra todas las apps detectadas y su configuración",
    "sort_label": "Orden:",
    "sort_arrival": "Por llegada",
    "sort_name": "Por nombre",
    "sort_count": "Por notificaciones",
    "start_daemon": "Iniciar daemon",
    "stop_daemon": "Detener daemon",
    "daemon_running": "Daemon: en ejecución",
    "daemon_stopped": "Daemon: detenido",
    "inherited": "Heredado",
    "own_sound_tooltip": (
        "Esta app ya reproduce su propio sonido. NotifySound no la "
        "duplica por defecto (el switch está desactivado). Si activas "
        "el switch, se oirán ambos sonidos."
    ),
    "own_sound_label": "Tiene sonido propio",
    "volume_tooltip": "Volumen de esta aplicación (0-100 %)",
    "test_tooltip": "Probar",
    "rename_tooltip": "Renombrar",
    "info_tooltip": "Información",
    "rules_tooltip": "Editar reglas de sonido por contenido de esta aplicación",
    "remove_tooltip": "Eliminar de la lista",
    "save_button": "Guardar",
    "reset_button": "Restablecer",
    "merge_confirm": "Fusionar",
    "cancel_button": "Cancelar",
    "merge_message": (
        "El alias «{alias}» ya lo usa «{owner_display}».\n"
        "¿Fusionar? Se eliminará «{source_display}», sus "
        "notificaciones futuras se atribuirán a «{owner_display}» y "
        "«{source}» se añadirá como sinónimo."
    ),
    "rules_dialog_title": "Reglas de {name}",
    "add_rule_button": "Añadir regla",
    "close_button": "Cerrar",
    "drag_handle_tooltip": "Arrastrar para reordenar",
    "rule_field_label": "Campo",
    "rule_op_label": "Operador",
    "rule_value_label": "Valor",
    "rule_action_label": "Acción",
    "rule_sound_label": "Sonido",
    "delete_rule_tooltip": "Eliminar regla",
    "rule_field_body": "Cuerpo",
    "rule_field_summary": "Título",
    "rule_field_urgency": "Urgencia",
    "rule_op_contains": "Contiene",
    "rule_op_regex": "Regex",
    "rule_op_eq": "Igual",
    "rule_op_starts_with": "Empieza con",
    "rule_op_ends_with": "Termina con",
    "rule_action_sound": "Reproducir",
    "rule_action_silence": "Silenciar",
    "urgency_low": "Baja",
    "urgency_normal": "Normal",
    "urgency_critical": "Crítica",
    "synonyms_header": "Sinónimos (pulsa Restaurar para separar):",
    "restore_button": "Restaurar",
    "reset_confirm_message": (
        "¿Vaciar la lista de aplicaciones detectadas y su configuración "
        "por-app? Se borrarán todos los renombrados, sinónimos y "
        "contadores. Las apps se volverán a detectar al recibir "
        "notificaciones."
    ),
    "reset_confirm_button": "Vaciar lista",
    "info_notification_name": "Nombre de notificación: {app_name}",
    "info_displayed_as": "Mostrado como: {display}",
    "info_sender_process": "Proceso emisor: {comm}",
    "info_own_sound": "Sonido propio: {value}",
    "info_yes": "sí",
    "info_no": "no",
    "info_synonym_count": "Número de sinónimos: {count}",
    "info_notification_count": "Notificaciones: {count}",
    "info_last_seen": "Última vista: {stamp}",
    "info_legacy_note": (
        "Esta aplicación se detectó antes de la v0.1.9; aún no "
        "se ha observado ninguna notificación suya con el daemon "
        "actual. Al recibirla se rellenarán proceso, contador y "
        "última vista."
    ),
    "remove_custom_button": "Quitar",
    "choose_sound_file": "Elegir archivo de sonido",
    "audio_filter": "Audio",
    "language_label": "Idioma:",
}

_current_language = DEFAULT_LANGUAGE


def set_language(language):
    """Fija el idioma activo; los valores no soportados se ignoran."""
    global _current_language
    if language in SUPPORTED_LANGUAGES:
        _current_language = language


def get_language():
    """Devuelve el idioma activo ("en" o "es")."""
    return _current_language


def t(key, **kwargs):
    """Traduce una clave del catálogo al idioma activo.

    Soporta interpolación con ``.format(**kwargs)``; una clave
    desconocida se devuelve tal cual (fallback al inglés si la clave
    falta en el idioma activo).
    """
    catalog = ES if _current_language == "es" else EN
    template = catalog.get(key, EN.get(key, key))
    if kwargs:
        return template.format(**kwargs)
    return template
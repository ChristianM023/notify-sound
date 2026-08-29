import os
import subprocess
import sys
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GObject, Gdk, GLib, Gtk, Pango

from . import config, player, sounds

INHERITED = "__inherited__"
RULE_FIELDS = (
    ("body", "Cuerpo"),
    ("summary", "Título"),
    ("urgency", "Urgencia"),
)
RULE_OPS = (
    ("contains", "Contiene"),
    ("regex", "Regex"),
    ("eq", "Igual"),
    ("starts_with", "Empieza con"),
    ("ends_with", "Termina con"),
)
RULE_ACTIONS = (("sound", "Reproducir"), ("silence", "Silenciar"))
STATE_INTERVAL_MS = 2000
FORMATS_HINT = (
    "Formatos: OGG, WAV y FLAC. MP3/M4A/AAC necesitan gst-launch-1.0, "
    "ffplay, mpv o mpg123."
)


def _entrypoint():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "notify-sound",
    )


def _spawn(command):
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class NotifyWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app, title="NotifySound",
            default_width=720, default_height=950,
        )
        self.cfg = config.load_config()
        self.theme_ids = sorted(sounds.list_sounds().keys())
        if not self.theme_ids:
            self.theme_ids = ["message"]
        self.app_rows = {}
        self.custom_rows = {}
        self._rebuilding = False
        self.sort_dropdown = None
        self._build_ui()
        GLib.timeout_add(STATE_INTERVAL_MS, self._refresh_state)

    def _choices(self):
        choices = [(sound_id, sound_id) for sound_id in self.theme_ids]
        for path in self.cfg.get("custom_sounds", []):
            choices.append((os.path.basename(path), path))
        return choices

    def _choice_index(self, value):
        for index, (_, choice_value) in enumerate(self._choices()):
            if choice_value == value:
                return index
        return None

    def _choice_value(self, index):
        choices = self._choices()
        if 0 <= index < len(choices):
            return choices[index][1]
        return None

    def _build_ui(self):
        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=12, margin_bottom=12, margin_start=14, margin_end=14,
        )
        self.set_child(root)

        master_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        master_label = Gtk.Label(label="Sonido de notificaciones", hexpand=True, xalign=0)
        self.master_switch = Gtk.Switch(active=bool(self.cfg.get("enabled", True)))
        self.master_switch.props.valign = Gtk.Align.CENTER
        self.master_switch.connect("notify::active", self._on_master_toggled)
        master_row.append(master_label)
        master_row.append(self.master_switch)
        root.append(master_row)

        autostart_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        autostart_label = Gtk.Label(
            label="Iniciar con la sesión", hexpand=True, xalign=0
        )
        self.autostart_switch = Gtk.Switch(
            active=bool(config.autostart_enabled())
        )
        self.autostart_switch.props.valign = Gtk.Align.CENTER
        self.autostart_switch.connect(
            "notify::active", self._on_autostart_toggled
        )
        autostart_row.append(autostart_label)
        autostart_row.append(self.autostart_switch)
        root.append(autostart_row)

        sound_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sound_label = Gtk.Label(label="Sonido global:", xalign=0)
        self.sound_dropdown = Gtk.DropDown()
        self.sound_dropdown.connect("notify::selected", self._on_sound_changed)
        test_button = Gtk.Button(label="Probar")
        test_button.connect("clicked", self._on_test)
        sound_row.append(sound_label)
        sound_row.append(self.sound_dropdown)
        sound_row.append(test_button)
        root.append(sound_row)

        custom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        add_button = Gtk.Button(label="Añadir sonido propio...")
        add_button.connect("clicked", self._on_add_custom)
        custom_row.append(add_button)
        root.append(custom_row)

        formats_label = Gtk.Label(label=FORMATS_HINT, xalign=0, wrap=True)
        formats_label.add_css_class("dim-label")
        root.append(formats_label)

        self.custom_box = Gtk.ListBox()
        self.custom_box.set_selection_mode(Gtk.SelectionMode.NONE)
        custom_scroll = Gtk.ScrolledWindow(
            vexpand=False, hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        custom_scroll.set_min_content_height(120)
        custom_scroll.set_max_content_height(180)
        custom_scroll.set_propagate_natural_height(False)
        custom_scroll.set_child(self.custom_box)
        root.append(custom_scroll)

        debounce_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        debounce_label = Gtk.Label(label="Anti-ráfaga (s):", xalign=0)
        debounce_adjustment = Gtk.Adjustment(
            value=float(self.cfg.get("debounce_window", 2.0)),
            lower=0, upper=60, step_increment=0.5, page_increment=5, page_size=0,
        )
        self.debounce_spin = Gtk.SpinButton(
            adjustment=debounce_adjustment, climb_rate=0, digits=1
        )
        self.debounce_spin.set_tooltip_text(
            "Segundos entre sonidos de una misma app. 0 desactiva. "
            "Dentro de la ventana, solo suena la primera notificación "
            "de una ráfaga."
        )
        self.debounce_spin.props.valign = Gtk.Align.CENTER
        self.debounce_spin.connect("value-changed", self._on_debounce_changed)
        debounce_row.append(debounce_label)
        debounce_row.append(self.debounce_spin)
        root.append(debounce_row)

        separator = Gtk.Separator()
        root.append(separator)

        apps_header_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )
        apps_header = Gtk.Label(label="Aplicaciones", xalign=0, hexpand=True)
        apps_header.add_css_class("heading")
        self.reset_apps_button = Gtk.Button(label="Vaciar lista")
        self.reset_apps_button.set_tooltip_text(
            "Borra todas las apps detectadas y su configuración"
        )
        self.reset_apps_button.props.valign = Gtk.Align.CENTER
        self.reset_apps_button.connect("clicked", self._on_reset_apps)
        sort_label = Gtk.Label(label="Orden:", xalign=0)
        self.sort_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new(
                ["Por llegada", "Por nombre", "Por notificaciones"]
            )
        )
        self.sort_dropdown.props.valign = Gtk.Align.CENTER
        self.sort_dropdown.connect(
            "notify::selected", self._on_sort_changed
        )
        apps_header_row.append(apps_header)
        apps_header_row.append(sort_label)
        apps_header_row.append(self.sort_dropdown)
        apps_header_row.append(self.reset_apps_button)
        root.append(apps_header_row)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.apps_list = Gtk.ListBox()
        self.apps_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self.apps_list)
        root.append(scroll)

        daemon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.start_button = Gtk.Button(label="Iniciar daemon")
        self.start_button.connect("clicked", self._on_start_daemon)
        self.stop_button = Gtk.Button(label="Detener daemon")
        self.stop_button.connect("clicked", self._on_stop_daemon)
        self.state_label = Gtk.Label(label="", xalign=0, hexpand=True)
        daemon_row.append(self.start_button)
        daemon_row.append(self.stop_button)
        daemon_row.append(self.state_label)
        root.append(daemon_row)

        self._populate_apps()
        self._refresh_custom_list()
        self._rebuild_all_dropdowns()

    def _populate_apps(self):
        seen = config.load_state().get("apps_seen", [])
        for app_name in seen:
            self._ensure_app_row(app_name)
        self._refresh_app_sensitivity()

    def _on_sort_changed(self, dropdown, param):
        self._reorder_apps(dropdown.get_selected())

    def _reorder_apps(self, sort_mode):
        ordered = self._sorted_app_names(sort_mode)
        for app_name in ordered:
            entry = self.app_rows.get(app_name)
            if entry is None:
                continue
            self.apps_list.remove(entry["row"])
            self.apps_list.append(entry["row"])

    def _sorted_app_names(self, sort_mode):
        seen = config.load_state().get("apps_seen", [])
        if sort_mode == 1:
            return sorted(seen, key=self._sort_key_name)
        if sort_mode == 2:
            return sorted(seen, key=self._sort_key_count, reverse=True)
        return list(seen)

    def _sort_key_name(self, app_name):
        return (self._display_name(app_name).lower(), app_name)

    def _sort_key_count(self, app_name):
        meta = config.load_state().get("app_meta", {}).get(app_name, {})
        return (meta.get("seen_count", 0), app_name)

    def _ensure_app_row(self, app_name):
        if app_name in self.app_rows:
            return
        apps_cfg = self.cfg.get("apps", {})
        app_cfg = apps_cfg.get(app_name, {})
        # OWN-001: estado inicial del switch. Si el usuario ya configuro la
        # app en config.json, se respeta su eleccion; si es nueva (primera
        # vez o tras vaciar lista) y la app reproduce su propio sonido, el
        # switch aparece desactivado por defecto (pero siempre editable).
        meta = config.load_state().get("app_meta", {}).get(app_name, {})
        has_own_sound = bool(meta.get("has_own_sound", False))
        if app_name in apps_cfg:
            switch_active = bool(app_cfg.get("enabled", True))
        else:
            switch_active = not has_own_sound
        row = Gtk.ListBoxRow()
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
            margin_top=4, margin_bottom=4,
        )
        name_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
        )
        name_label = Gtk.Label(
            label=self._display_name(app_name),
            hexpand=True, xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            tooltip_text=app_name,
        )
        name_label.set_width_chars(28)
        name_label.set_max_width_chars(50)
        # OWN-001: etiqueta/icono "Tiene sonido propio", visible siempre que
        # la app reproduzca su propio sonido (informa, no bloquea).
        own_sound_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4, hexpand=True,
        )
        own_sound_box.set_tooltip_text(
            "Esta app ya reproduce su propio sonido. NotifySound no la "
            "duplica por defecto (el switch está desactivado). Si activas "
            "el switch, se oirán ambos sonidos."
        )
        own_sound_icon = Gtk.Image(icon_name="audio-x-generic-symbolic")
        own_sound_icon.props.valign = Gtk.Align.CENTER
        own_sound_label = Gtk.Label(label="Tiene sonido propio", xalign=0)
        own_sound_label.add_css_class("dim-label")
        own_sound_label.props.valign = Gtk.Align.CENTER
        own_sound_box.append(own_sound_icon)
        own_sound_box.append(own_sound_label)
        own_sound_box.set_visible(has_own_sound)
        name_box.append(name_label)
        name_box.append(own_sound_box)
        app_sound_dropdown = Gtk.DropDown()
        app_sound_dropdown.props.valign = Gtk.Align.CENTER
        app_sound_dropdown.connect(
            "notify::selected", self._on_app_sound_changed, app_name
        )
        volume_adjustment = Gtk.Adjustment(
            value=app_cfg.get("volume", 100), lower=0, upper=100,
            step_increment=1, page_increment=10, page_size=0,
        )
        volume_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=volume_adjustment,
        )
        volume_scale.set_tooltip_text("Volumen de esta aplicación (0-100 %)")
        volume_scale.props.valign = Gtk.Align.CENTER
        volume_scale.set_hexpand(False)
        volume_scale.set_size_request(120, -1)
        volume_scale.connect(
            "value-changed", self._on_app_volume_changed, app_name
        )
        app_test_button = Gtk.Button()
        app_test_button.set_icon_name("media-playback-start-symbolic")
        app_test_button.set_tooltip_text("Probar")
        app_test_button.props.valign = Gtk.Align.CENTER
        app_test_button.connect("clicked", self._on_test_app, app_name)
        rename_button = Gtk.Button()
        rename_button.set_icon_name("document-edit-symbolic")
        rename_button.set_tooltip_text("Renombrar")
        rename_button.props.valign = Gtk.Align.CENTER
        rename_button.connect("clicked", self._on_app_rename, app_name)
        info_button = Gtk.Button()
        info_button.set_icon_name("dialog-information-symbolic")
        info_button.set_tooltip_text("Información")
        info_button.props.valign = Gtk.Align.CENTER
        info_button.connect("clicked", self._on_app_info, app_name)
        rules_button = Gtk.Button()
        rules_button.set_icon_name("view-list-symbolic")
        rules_button.set_tooltip_text(
            "Editar reglas de sonido por contenido de esta aplicación"
        )
        rules_button.props.valign = Gtk.Align.CENTER
        rules_button.connect("clicked", self._on_app_rules, app_name)
        app_switch = Gtk.Switch(active=switch_active)
        app_switch.props.valign = Gtk.Align.CENTER
        app_switch.connect("notify::active", self._on_app_toggled, app_name)
        remove_button = Gtk.Button()
        remove_button.set_icon_name("user-trash-symbolic")
        remove_button.set_tooltip_text("Eliminar de la lista")
        remove_button.props.valign = Gtk.Align.CENTER
        remove_button.connect("clicked", self._on_app_remove, app_name)
        box.append(name_box)
        box.append(app_sound_dropdown)
        box.append(volume_scale)
        box.append(app_test_button)
        box.append(rename_button)
        box.append(info_button)
        box.append(rules_button)
        box.append(app_switch)
        box.append(remove_button)
        row.set_child(box)
        self.apps_list.append(row)
        self.app_rows[app_name] = {
            "row": row,
            "switch": app_switch,
            "dropdown": app_sound_dropdown,
            "volume_scale": volume_scale,
            "name_label": name_label,
            "own_sound_box": own_sound_box,
            "rules_button": rules_button,
        }
        self._populate_app_dropdown(app_name, self.app_rows[app_name])

    def _populate_app_dropdown(self, app_name, entry):
        displays = [display for display, _ in self._choices()]
        previous = self._rebuilding
        self._rebuilding = True
        try:
            entry["dropdown"].set_model(
                Gtk.StringList.new([INHERITED] + displays)
            )
            app_sound = self.cfg.get("apps", {}).get(app_name, {}).get("sound")
            app_index = self._choice_index(app_sound)
            entry["dropdown"].set_selected(
                0 if app_index is None else app_index + 1
            )
        finally:
            self._rebuilding = previous

    def _display_name(self, app_name):
        name = self.cfg.get("apps", {}).get(app_name, {}).get("name")
        if isinstance(name, str) and name:
            return name
        return app_name

    def _on_app_rename(self, button, app_name):
        entry_store = self.app_rows[app_name]
        popover = entry_store.get("rename_popover")
        if popover is None:
            popover = Gtk.Popover()
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8,
                margin_top=8, margin_bottom=8, margin_start=8, margin_end=8,
            )
            text_entry = Gtk.Entry(
                max_length=config.MAX_APP_NAME_LENGTH, width_chars=28,
            )
            actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END,
            )
            save_button = Gtk.Button(label="Guardar")
            reset_button = Gtk.Button(label="Restablecer")
            save_button.connect(
                "clicked", self._on_app_rename_save, app_name, text_entry, popover
            )
            reset_button.connect(
                "clicked", self._on_app_rename_reset, app_name, popover
            )
            actions.append(reset_button)
            actions.append(save_button)
            content.append(text_entry)
            content.append(actions)
            popover.set_child(content)
            popover.set_parent(button)
            entry_store["rename_popover"] = popover
            entry_store["rename_entry"] = text_entry
        entry_store["rename_entry"].set_text(self._display_name(app_name))
        popover.popup()

    def _on_app_rename_save(self, button, app_name, entry, popover):
        new_name = entry.get_text().strip()
        if not new_name or len(new_name) > config.MAX_APP_NAME_LENGTH:
            return
        owner = config._find_alias_owner(self.cfg, new_name)
        if owner and owner != app_name:
            popover.popdown()
            self._prompt_merge_alias(app_name, new_name, owner)
            return
        app_entry = self.cfg["apps"].setdefault(
            app_name, {"enabled": True, "sound": None}
        )
        app_entry["name"] = new_name
        self._save()
        self.app_rows[app_name]["name_label"].set_text(new_name)
        popover.popdown()

    def _on_app_rename_reset(self, button, app_name, popover):
        app_entry = self.cfg.get("apps", {}).get(app_name)
        if isinstance(app_entry, dict) and "name" in app_entry:
            del app_entry["name"]
            self._save()
            self.app_rows[app_name]["name_label"].set_text(app_name)
        popover.popdown()

    def _prompt_merge_alias(self, source, alias, target):
        owner_display = self._display_name(target)
        source_display = self._display_name(source)
        entry_store = self.app_rows[source]
        popover = entry_store.get("merge_popover")
        if popover is None:
            popover = Gtk.Popover()
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=10,
                margin_top=10, margin_bottom=10, margin_start=12, margin_end=12,
            )
            message = Gtk.Label(
                wrap=True, xalign=0, max_width_chars=42,
            )
            actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                halign=Gtk.Align.END,
            )
            confirm_button = Gtk.Button(label="Fusionar")
            cancel_button = Gtk.Button(label="Cancelar")
            confirm_button.add_css_class("destructive-action")
            confirm_button.connect(
                "clicked", self._on_merge_confirm, source, target, popover
            )
            cancel_button.connect("clicked", self._on_merge_cancel, popover)
            actions.append(cancel_button)
            actions.append(confirm_button)
            content.append(message)
            content.append(actions)
            popover.set_child(content)
            popover.set_parent(entry_store["row"])
            entry_store["merge_popover"] = popover
            entry_store["merge_message"] = message
        entry_store["merge_message"].set_text(
            f"El alias «{alias}» ya lo usa «{owner_display}».\n"
            f"¿Fusionar? Se eliminará «{source_display}», sus "
            f"notificaciones futuras se atribuirán a «{owner_display}» y "
            f"«{source}» se añadirá como sinónimo."
        )
        popover.popup()

    def _on_merge_confirm(self, button, source, target, popover):
        popover.popdown()
        self._merge_app_into(source, target)

    def _on_merge_cancel(self, button, popover):
        popover.popdown()

    def _merge_app_into(self, source, target):
        if source == target:
            return
        apps = self.cfg.get("apps", {})
        if source not in apps:
            return
        source_cfg = apps.get(source, {})
        target_cfg = apps.setdefault(target, {"enabled": True, "sound": None})
        if source_cfg.get("enabled") is False:
            target_cfg["enabled"] = False
        if target_cfg.get("sound") is None and source_cfg.get("sound") is not None:
            target_cfg["sound"] = source_cfg["sound"]
        if "volume" not in target_cfg and "volume" in source_cfg:
            target_cfg["volume"] = source_cfg["volume"]
        synonyms = list(target_cfg.get("synonyms") or [])
        if source not in synonyms:
            synonyms.append(source)
        seen = set()
        clean = []
        for item in synonyms:
            if item not in seen and 0 < len(item) <= config.MAX_APP_NAME_LENGTH:
                seen.add(item)
                clean.append(item)
        if clean:
            target_cfg["synonyms"] = clean[:config.MAX_SYNONYMS]
        else:
            target_cfg.pop("synonyms", None)
        del apps[source]
        self._save()
        state = config.load_state()
        apps_seen = [n for n in state.get("apps_seen", []) if n != source]
        app_meta = dict(state.get("app_meta", {}))
        app_meta.pop(source, None)
        config.save_state({"apps_seen": apps_seen, "app_meta": app_meta})
        entry = self.app_rows.pop(source, None)
        if entry is not None:
            self.apps_list.remove(entry["row"])

    def _on_app_remove(self, button, app_name):
        apps = self.cfg.get("apps", {})
        apps.pop(app_name, None)
        for other_cfg in apps.values():
            if isinstance(other_cfg, dict):
                other_syn = list(other_cfg.get("synonyms") or [])
                if app_name in other_syn:
                    other_syn.remove(app_name)
                    if other_syn:
                        other_cfg["synonyms"] = other_syn[: config.MAX_SYNONYMS]
                    else:
                        other_cfg.pop("synonyms", None)
        self._save()
        state = config.load_state()
        apps_seen = [n for n in state.get("apps_seen", []) if n != app_name]
        app_meta = dict(state.get("app_meta", {}))
        app_meta.pop(app_name, None)
        config.save_state({"apps_seen": apps_seen, "app_meta": app_meta})
        entry = self.app_rows.pop(app_name, None)
        if entry is not None:
            self.apps_list.remove(entry["row"])

    def _get_app_rules(self, app_name):
        """Devuelve la lista de reglas de una app (RULE-001)."""
        app_cfg = self.cfg.get("apps", {}).get(app_name)
        if not isinstance(app_cfg, dict):
            return []
        rules = app_cfg.get("rules")
        return rules if isinstance(rules, list) else []

    def _set_app_rules(self, app_name, rules):
        """Guarda la lista de reglas en la config de la app.

        Una lista vacia elimina la clave ``rules`` (mismo criterio que
        ``_normalize_app`` en config.py).
        """
        app_cfg = self.cfg.setdefault("apps", {}).setdefault(
            app_name, {"enabled": True, "sound": None}
        )
        if rules:
            app_cfg["rules"] = rules
        else:
            app_cfg.pop("rules", None)

    def _normalize_rule_value(self, field, op, value):
        """Convierte el valor de una regla a int para eq+urgency (0/1/2).

        Cualquier otro caso devuelve el valor como string; un valor
        invalido para eq+urgency se guarda como string y no matcheara
        (el usuario lo corrige).
        """
        if op == "eq" and field == "urgency":
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = None
            if parsed in (0, 1, 2):
                return parsed
        return str(value)

    def _set_app_rule(self, app_name, index, field, op, value, action, sound):
        """Actualiza la regla en ``index`` y persiste (RULE-001)."""
        rules = self._get_app_rules(app_name)
        if not 0 <= index < len(rules):
            return
        rule = rules[index]
        rule["match"] = {
            "field": field,
            "op": op,
            "value": self._normalize_rule_value(field, op, value),
        }
        rule["action"] = action
        if action == "sound":
            rule["sound"] = sound
        else:
            rule.pop("sound", None)
        self._save()

    def _add_app_rule(self, app_name):
        """Añade una regla default a la app y persiste (RULE-001).

        Respeta ``config.MAX_RULES``: si la app ya tiene el maximo, no
        se añade nada.
        """
        rules = self._get_app_rules(app_name)
        if len(rules) >= config.MAX_RULES:
            return
        choices = self._choices()
        default_sound = choices[0][1] if choices else None
        rules.append(
            {
                "match": {"field": "body", "op": "contains", "value": ""},
                "action": "sound",
                "sound": default_sound,
            }
        )
        self._set_app_rules(app_name, rules)
        self._save()

    def _remove_app_rule(self, app_name, index):
        """Elimina la regla en ``index`` y persiste (RULE-001)."""
        rules = self._get_app_rules(app_name)
        if not 0 <= index < len(rules):
            return
        del rules[index]
        self._set_app_rules(app_name, rules)
        self._save()

    def _move_app_rule(self, app_name, source_index, target_index):
        """Mueve la regla de ``source_index`` a ``target_index`` y persiste.

        El orden importa: la primera regla que matchea gana (RULE-001).
        Indices invalidos o un movimiento sin cambio no guardan nada.
        """
        rules = self._get_app_rules(app_name)
        if not 0 <= source_index < len(rules):
            return
        if not 0 <= target_index < len(rules):
            return
        if source_index == target_index:
            return
        rules.insert(target_index, rules.pop(source_index))
        self._set_app_rules(app_name, rules)
        self._save()

    def _rule_field_index(self, field):
        for index, (value, _) in enumerate(RULE_FIELDS):
            if value == field:
                return index
        return 0

    def _rule_op_index(self, op):
        for index, (value, _) in enumerate(RULE_OPS):
            if value == op:
                return index
        return 0

    def _rule_action_index(self, action):
        for index, (value, _) in enumerate(RULE_ACTIONS):
            if value == action:
                return index
        return 0

    def _on_app_rules(self, button, app_name):
        """Abre el diálogo de reglas por contenido de una app (RULE-001)."""
        dialog = Gtk.Window(title=f"Reglas de {self._display_name(app_name)}")
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_default_size(680, 480)
        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=12, margin_bottom=12, margin_start=14, margin_end=14,
        )
        dialog.set_child(root)
        rules_box = Gtk.ListBox()
        rules_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(rules_box)
        root.append(scroll)
        actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            halign=Gtk.Align.END,
        )
        add_button = Gtk.Button(label="Añadir regla")
        add_button.connect(
            "clicked", self._on_rules_add_clicked, app_name, rules_box
        )
        close_button = Gtk.Button(label="Cerrar")
        close_button.connect("clicked", lambda *_: dialog.close())
        actions.append(add_button)
        actions.append(close_button)
        root.append(actions)
        self._rebuild_rules_list(app_name, rules_box)
        dialog.present()

    def _on_rules_add_clicked(self, button, app_name, rules_box):
        self._add_app_rule(app_name)
        self._rebuild_rules_list(app_name, rules_box)

    def _rebuild_rules_list(self, app_name, rules_box):
        for child in list(rules_box):
            rules_box.remove(child)
        for rule in self._get_app_rules(app_name):
            rules_box.append(self._build_rule_row(app_name, rule, rules_box))

    def _build_rule_row(self, app_name, rule, rules_box):
        row = Gtk.ListBoxRow()
        container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4,
            margin_top=4, margin_bottom=4,
        )
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
        handle.props.valign = Gtk.Align.CENTER
        drag_handle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        drag_handle.set_size_request(24, -1)
        drag_handle.props.valign = Gtk.Align.CENTER
        drag_handle.set_tooltip_text("Arrastrar para reordenar")
        drag_handle.append(handle)
        row_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
        )
        row_box.append(drag_handle)
        row_box.append(container)
        match = rule.get("match", {})
        field = match.get("field", "body")
        op = match.get("op", "contains")
        value = match.get("value", "")
        action = rule.get("action", "sound")
        sound = rule.get("sound")
        field_label = Gtk.Label(label="Campo", xalign=0)
        field_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _, label in RULE_FIELDS])
        )
        field_dropdown.set_selected(self._rule_field_index(field))
        op_label = Gtk.Label(label="Operador", xalign=0)
        op_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _, label in RULE_OPS])
        )
        op_dropdown.set_selected(self._rule_op_index(op))
        value_label = Gtk.Label(label="Valor", xalign=0)
        value_entry = Gtk.Entry(text=str(value), width_chars=16)
        urgency_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new(["Baja", "Normal", "Crítica"])
        )
        urgency_dropdown.set_selected(self._urgency_index(value))
        is_urgency = field == "urgency"
        value_entry.set_visible(not is_urgency)
        urgency_dropdown.set_visible(is_urgency)
        if is_urgency:
            # Urgencia solo admite "Igual" (eq): desactivar el desplegable
            # de Operador y forzar la seleccion (RULE-001).
            op_dropdown.set_sensitive(False)
            op_dropdown.set_selected(self._rule_op_index("eq"))
        match_line = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
        )
        match_line.append(field_label)
        match_line.append(field_dropdown)
        match_line.append(op_label)
        match_line.append(op_dropdown)
        match_line.append(value_label)
        match_line.append(value_entry)
        match_line.append(urgency_dropdown)
        container.append(match_line)
        action_label = Gtk.Label(label="Acción", xalign=0)
        action_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _, label in RULE_ACTIONS])
        )
        action_dropdown.set_selected(self._rule_action_index(action))
        sound_label = Gtk.Label(label="Sonido", xalign=0)
        sound_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new(
                [display for display, _ in self._choices()]
            )
        )
        sound_index = self._choice_index(sound)
        sound_dropdown.set_selected(0 if sound_index is None else sound_index)
        sound_dropdown.set_visible(action == "sound")
        delete_button = Gtk.Button()
        delete_button.set_icon_name("user-trash-symbolic")
        delete_button.set_tooltip_text("Eliminar regla")
        delete_button.props.valign = Gtk.Align.CENTER
        action_line = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
        )
        action_line.append(action_label)
        action_line.append(action_dropdown)
        action_line.append(sound_label)
        action_line.append(sound_dropdown)
        action_line.append(delete_button)
        container.append(action_line)
        widgets = {
            "row": row,
            "handle": drag_handle,
            "field": field_dropdown,
            "op": op_dropdown,
            "value": value_entry,
            "urgency": urgency_dropdown,
            "action": action_dropdown,
            "sound": sound_dropdown,
            "field_value": field,
        }
        row.widgets = widgets
        field_dropdown.connect(
            "notify::selected",
            lambda *_: self._on_rule_changed(app_name, widgets),
        )
        op_dropdown.connect(
            "notify::selected",
            lambda *_: self._on_rule_changed(app_name, widgets),
        )
        value_entry.connect(
            "changed",
            lambda *_: self._on_rule_changed(app_name, widgets),
        )
        urgency_dropdown.connect(
            "notify::selected",
            lambda *_: self._on_rule_changed(app_name, widgets),
        )
        action_dropdown.connect(
            "notify::selected",
            lambda *_: self._on_rule_changed(app_name, widgets),
        )
        sound_dropdown.connect(
            "notify::selected",
            lambda *_: self._on_rule_changed(app_name, widgets),
        )
        delete_button.connect(
            "clicked",
            lambda *_: self._on_rule_delete_clicked(
                app_name, widgets, rules_box
            ),
        )
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_rule_drag_prepare)
        # El DragSource vive en el handle, no en el row: los widgets
        # interactivos (DropDown, Entry, Button) capturan el press y
        # bloquearian el drag si estuviera en la fila completa.
        drag_handle.add_controller(drag_source)
        drop_target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        drop_target.connect("drop", self._on_rule_drop, app_name, rules_box)
        row.add_controller(drop_target)
        row.set_child(row_box)
        return row

    def _on_rule_drag_prepare(self, source, x, y):
        """Empaqueta el indice de la fila origen al iniciar el drag (RULE-001).

        El DragSource vive en el handle de arrastre; se sube por la
        jerarquia de widgets hasta la ListBoxRow para obtener el indice.
        """
        widget = source.get_widget()
        while widget is not None and not isinstance(widget, Gtk.ListBoxRow):
            widget = widget.get_parent()
        if widget is None:
            return None
        value = GObject.Value(GObject.TYPE_INT, widget.get_index())
        return Gdk.ContentProvider.new_for_value(value)

    def _on_rule_drop(self, target, value, x, y, app_name, rules_box):
        """Reordena la regla arrastrada al indice de la fila destino (RULE-001)."""
        if isinstance(value, GObject.Value):
            source_index = value.get_int()
        else:
            source_index = int(value)
        row = target.get_widget()
        target_index = row.get_index()
        if source_index == target_index:
            return False
        self._move_app_rule(app_name, source_index, target_index)
        self._rebuild_rules_list(app_name, rules_box)
        return True

    def _rule_row_values(self, widgets):
        field = RULE_FIELDS[widgets["field"].get_selected()][0]
        op = RULE_OPS[widgets["op"].get_selected()][0]
        if field == "urgency":
            value = widgets["urgency"].get_selected()
        else:
            value = widgets["value"].get_text()
        action = RULE_ACTIONS[widgets["action"].get_selected()][0]
        sound = None
        if action == "sound":
            sound = self._choice_value(widgets["sound"].get_selected())
        return field, op, value, action, sound

    def _urgency_index(self, value):
        """Indice del dropdown de urgencia para un valor de regla.

        Acepta int o string "0"/"1"/"2"; cualquier otro valor cae en
        "Normal" (1) como default.
        """
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = None
        if parsed in (0, 1, 2):
            return parsed
        return 1

    def _sync_rule_value_widget(self, app_name, index, widgets, field):
        """Sincroniza el widget de Valor visible con el valor previo de la
        regla cuando cambia el Campo (RULE-001).

        urgency -> dropdown con la opcion correspondiente (default
        "Normal"); resto -> entry con el valor como string.
        """
        rules = self._get_app_rules(app_name)
        if not 0 <= index < len(rules):
            return
        prev_value = rules[index].get("match", {}).get("value", "")
        if field == "urgency":
            widgets["value"].set_visible(False)
            widgets["urgency"].set_visible(True)
            widgets["urgency"].set_selected(self._urgency_index(prev_value))
        else:
            widgets["urgency"].set_visible(False)
            widgets["value"].set_visible(True)
            widgets["value"].set_text(str(prev_value))

    def _sync_rule_op_widget(self, widgets, field):
        """Sincroniza el desplegable de Operador con el Campo (RULE-001).

        Para urgency el unico operador valido es "Igual" (eq): el
        desplegable se desactiva y se fuerza la seleccion. Para el resto
        se reactiva con las 5 opciones.
        """
        if field == "urgency":
            widgets["op"].set_sensitive(False)
            widgets["op"].set_selected(self._rule_op_index("eq"))
        else:
            widgets["op"].set_sensitive(True)

    def _on_rule_changed(self, app_name, widgets):
        index = widgets["row"].get_index()
        field, op, value, action, sound = self._rule_row_values(widgets)
        if field != widgets.get("field_value"):
            # El campo cambio: marcar el nuevo campo antes de sincronizar
            # para que las senales anidadas (changed/notify::selected) no
            # vuelvan a entrar en la sincronizacion.
            widgets["field_value"] = field
            self._sync_rule_value_widget(app_name, index, widgets, field)
            self._sync_rule_op_widget(widgets, field)
            field, op, value, action, sound = self._rule_row_values(widgets)
        self._set_app_rule(app_name, index, field, op, value, action, sound)
        widgets["sound"].set_visible(action == "sound")

    def _on_rule_delete_clicked(self, app_name, widgets, rules_box):
        index = widgets["row"].get_index()
        self._remove_app_rule(app_name, index)
        self._rebuild_rules_list(app_name, rules_box)

    def _on_app_info(self, button, app_name):
        entry_store = self.app_rows[app_name]
        popover = entry_store.get("info_popover")
        if popover is None:
            popover = Gtk.Popover()
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8,
                margin_top=10, margin_bottom=10, margin_start=12, margin_end=12,
            )
            label = Gtk.Label(wrap=True, xalign=0, max_width_chars=44)
            synonyms_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            content.append(label)
            content.append(synonyms_box)
            popover.set_child(content)
            popover.set_parent(button)
            entry_store["info_popover"] = popover
            entry_store["info_label"] = label
            entry_store["info_synonyms_box"] = synonyms_box
        entry_store["info_label"].set_text(self._format_app_info(app_name))
        self._refresh_info_synonyms(app_name, entry_store["info_synonyms_box"])
        popover.popup()

    def _refresh_info_synonyms(self, app_name, synonyms_box):
        for child in list(synonyms_box):
            synonyms_box.remove(child)
        app_cfg = self.cfg.get("apps", {}).get(app_name, {})
        synonyms = app_cfg.get("synonyms") or []
        if not synonyms:
            return
        header = Gtk.Label(
            label="Sinónimos (pulsa Restaurar para separar):",
            xalign=0, halign=Gtk.Align.START,
        )
        header.add_css_class("dim-label")
        synonyms_box.append(header)
        for syn in synonyms:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            )
            syn_label = Gtk.Label(
                label=syn, xalign=0, hexpand=True,
                ellipsize=Pango.EllipsizeMode.END, tooltip_text=syn,
            )
            restore_button = Gtk.Button(label="Restaurar")
            restore_button.props.valign = Gtk.Align.CENTER
            restore_button.connect(
                "clicked", self._on_synonym_restore, app_name, syn
            )
            row.append(syn_label)
            row.append(restore_button)
            synonyms_box.append(row)

    def _on_synonym_restore(self, button, app_name, synonym):
        app_cfg = self.cfg["apps"].get(app_name)
        if not isinstance(app_cfg, dict):
            return
        synonyms = list(app_cfg.get("synonyms") or [])
        if synonym in synonyms:
            synonyms.remove(synonym)
            if synonyms:
                app_cfg["synonyms"] = synonyms[: config.MAX_SYNONYMS]
            else:
                app_cfg.pop("synonyms", None)
        self._save()
        state = config.load_state()
        apps_seen = list(state.get("apps_seen", []))
        if synonym not in apps_seen:
            apps_seen.append(synonym)
        config.save_state(
            {"apps_seen": apps_seen, "app_meta": state.get("app_meta", {})}
        )
        self._ensure_app_row(synonym)
        self._refresh_app_sensitivity()
        popover = self.app_rows[app_name].get("info_popover")
        if popover is not None:
            synonyms_box = self.app_rows[app_name].get("info_synonyms_box")
            if synonyms_box is not None:
                self._refresh_info_synonyms(app_name, synonyms_box)
            self.app_rows[app_name]["info_label"].set_text(
                self._format_app_info(app_name)
            )

    def _on_reset_apps(self, button):
        popover = Gtk.Popover()
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=10, margin_bottom=10, margin_start=12, margin_end=12,
        )
        message = Gtk.Label(
            wrap=True, xalign=0, max_width_chars=44,
        )
        message.set_text(
            "¿Vaciar la lista de aplicaciones detectadas y su configuración "
            "por-app? Se borrarán todos los renombrados, sinónimos y contadores. "
            "Las apps se volverán a detectar al recibir notificaciones."
        )
        actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            halign=Gtk.Align.END,
        )
        confirm_button = Gtk.Button(label="Vaciar lista")
        cancel_button = Gtk.Button(label="Cancelar")
        confirm_button.add_css_class("destructive-action")
        confirm_button.connect("clicked", self._on_reset_apps_confirm, popover)
        cancel_button.connect("clicked", self._on_reset_apps_cancel, popover)
        actions.append(cancel_button)
        actions.append(confirm_button)
        content.append(message)
        content.append(actions)
        popover.set_child(content)
        popover.set_parent(button)
        popover.popup()

    def _on_reset_apps_confirm(self, button, popover):
        popover.popdown()
        self.cfg["apps"] = {}
        self._save()
        config.save_state({"apps_seen": [], "app_meta": {}})
        for entry in self.app_rows.values():
            self.apps_list.remove(entry["row"])
        self.app_rows = {}
        self._refresh_app_sensitivity()

    def _on_reset_apps_cancel(self, button, popover):
        popover.popdown()

    def _format_app_info(self, app_name):
        app_cfg = self.cfg.get("apps", {}).get(app_name, {})
        state = config.load_state()
        meta = state.get("app_meta", {}).get(app_name, {})
        comm = meta.get("comm")
        synonyms = app_cfg.get("synonyms") or []
        seen_count = meta.get("seen_count")
        last_seen = meta.get("last_seen")
        display = self._display_name(app_name)
        has_meta = bool(meta)
        lines = [f"Nombre de notificación: {app_name}"]
        if display != app_name:
            lines.append(f"Mostrado como: {display}")
        lines.append(f"Proceso emisor: {comm or '—'}")
        lines.append(
            f"Sonido propio: {'sí' if meta.get('has_own_sound') else 'no'}"
        )
        lines.append(f"Número de sinónimos: {len(synonyms)}")
        lines.append(f"Notificaciones: {seen_count if seen_count else '—'}")
        if last_seen:
            stamp = datetime.fromtimestamp(last_seen).strftime(
                "%Y-%m-%d %H:%M"
            )
        else:
            stamp = "—"
        lines.append(f"Última vista: {stamp}")
        if not has_meta:
            lines.append(
                "Esta aplicación se detectó antes de la v0.1.9; aún no"
                " se ha observado ninguna notificación suya con el daemon"
                " actual. Al recibirla se rellenarán proceso, contador y"
                " última vista."
            )
        return "\n".join(lines)

    def _rebuild_all_dropdowns(self):
        self._rebuilding = True
        displays = [display for display, _ in self._choices()]
        self.sound_dropdown.set_model(Gtk.StringList.new(displays))
        global_index = self._choice_index(self.cfg.get("sound"))
        self.sound_dropdown.set_selected(
            global_index if global_index is not None else 0
        )
        for app_name, entry in self.app_rows.items():
            entry["dropdown"].set_model(
                Gtk.StringList.new([INHERITED] + displays)
            )
            app_sound = self.cfg.get("apps", {}).get(app_name, {}).get("sound")
            app_index = self._choice_index(app_sound)
            entry["dropdown"].set_selected(
                0 if app_index is None else app_index + 1
            )
        self._rebuilding = False

    def _refresh_custom_list(self):
        for row in self.custom_rows.values():
            self.custom_box.remove(row)
        self.custom_rows = {}
        for path in self.cfg.get("custom_sounds", []):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                margin_top=2, margin_bottom=2,
            )
            label = Gtk.Label(
                label=os.path.basename(path), hexpand=True, xalign=0,
                tooltip_text=path,
                ellipsize=Pango.EllipsizeMode.END,
            )
            label.set_max_width_chars(40)
            remove_button = Gtk.Button(label="Quitar")
            remove_button.props.valign = Gtk.Align.CENTER
            remove_button.connect("clicked", self._on_remove_custom, path)
            box.append(label)
            box.append(remove_button)
            row.set_child(box)
            self.custom_box.append(row)
            self.custom_rows[path] = row

    def _refresh_app_sensitivity(self):
        enabled = bool(self.cfg.get("enabled", True))
        for entry in self.app_rows.values():
            entry["switch"].set_sensitive(enabled)
            entry["dropdown"].set_sensitive(enabled)
            entry["volume_scale"].set_sensitive(enabled)

    def _refresh_state(self):
        running = config.is_running()
        state = config.load_state()
        added = False
        for app_name in state.get("apps_seen", []):
            if app_name not in self.app_rows:
                self._ensure_app_row(app_name)
                added = True
        # OWN-001: visibilidad de la etiqueta "Tiene sonido propio" en vivo
        # para apps ya existentes cuando has_own_sound cambie en state.json.
        app_meta = state.get("app_meta", {})
        for app_name, entry in self.app_rows.items():
            own_sound_box = entry.get("own_sound_box")
            if own_sound_box is not None:
                meta = app_meta.get(app_name, {})
                own_sound_box.set_visible(
                    bool(meta.get("has_own_sound", False))
                )
        if added and self.sort_dropdown is not None:
            self._reorder_apps(self.sort_dropdown.get_selected())
        self.state_label.set_text(
            "Daemon: en ejecución" if running else "Daemon: detenido"
        )
        self.start_button.set_sensitive(not running)
        self.stop_button.set_sensitive(running)
        return True

    def _refresh_state_once(self):
        self._refresh_state()
        return False

    def _save(self):
        config.save_config(self.cfg)
        self._refresh_app_sensitivity()

    def _on_master_toggled(self, switch, param):
        self.cfg["enabled"] = switch.get_active()
        self._save()

    def _on_autostart_toggled(self, switch, param):
        enabled = switch.get_active()
        config.set_autostart(enabled)
        self.cfg["autostart"] = enabled
        self._save()

    def _on_sound_changed(self, dropdown, param):
        if self._rebuilding:
            return
        value = self._choice_value(dropdown.get_selected())
        if value:
            self.cfg["sound"] = value
            self._save()

    def _on_test(self, button):
        value = self._choice_value(self.sound_dropdown.get_selected())
        if value:
            player.play_choice(value)

    def _on_add_custom(self, button):
        dialog = Gtk.FileDialog(title="Elegir archivo de sonido")
        audio_filter = Gtk.FileFilter()
        audio_filter.set_name("Audio")
        audio_filter.add_mime_type("audio/*")
        dialog.set_default_filter(audio_filter)
        dialog.open(self, None, self._on_add_custom_done)

    def _on_add_custom_done(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        path = gfile.get_path()
        if path and path not in self.cfg.get("custom_sounds", []):
            self.cfg.setdefault("custom_sounds", []).append(path)
            self._save()
            self._refresh_custom_list()
            self._rebuild_all_dropdowns()

    def _on_remove_custom(self, button, path):
        self.cfg["custom_sounds"] = [
            p for p in self.cfg.get("custom_sounds", []) if p != path
        ]
        if self.cfg.get("sound") == path:
            self.cfg["sound"] = "message"
        for app_cfg in self.cfg.get("apps", {}).values():
            if app_cfg.get("sound") == path:
                app_cfg["sound"] = None
        self._save()
        self._refresh_custom_list()
        self._rebuild_all_dropdowns()

    def _on_debounce_changed(self, spin):
        self.cfg["debounce_window"] = float(spin.get_value())
        self._save()

    def _on_app_toggled(self, switch, param, app_name):
        entry = self.cfg["apps"].setdefault(
            app_name, {"enabled": True, "sound": None}
        )
        entry["enabled"] = switch.get_active()
        self._save()

    def _on_app_sound_changed(self, dropdown, param, app_name):
        if self._rebuilding:
            return
        selected = dropdown.get_selected()
        entry = self.cfg["apps"].setdefault(
            app_name, {"enabled": True, "sound": None}
        )
        if selected == 0:
            entry["sound"] = None
        else:
            entry["sound"] = self._choice_value(selected - 1)
        self._save()

    def _on_app_volume_changed(self, scale, app_name):
        if self._rebuilding:
            return
        entry = self.cfg["apps"].setdefault(
            app_name, {"enabled": True, "sound": None}
        )
        entry["volume"] = round(scale.get_value())
        self._save()

    def _on_test_app(self, button, app_name):
        app_cfg = self.cfg.get("apps", {}).get(app_name, {})
        choice = app_cfg.get("sound") or self.cfg.get("sound")
        if choice:
            player.play_choice(choice, volume=app_cfg.get("volume", 100))

    def _on_start_daemon(self, button):
        _spawn([sys.executable, _entrypoint(), "--daemon"])
        GLib.timeout_add(600, self._refresh_state_once)

    def _on_stop_daemon(self, button):
        from . import daemon as daemon_module

        daemon_module.quit_daemon()
        GLib.timeout_add(600, self._refresh_state_once)


class NotifyApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.notifysound.NotifySound")
        self.window = None
        self._held = False

    def do_activate(self):
        if self.window is None:
            self.window = NotifyWindow(self)
            self.window.connect("close-request", self._on_window_close)
        if not self._held:
            self.hold()
            self._held = True
        self.window.present()

    def _on_window_close(self, window):
        if self.window is window:
            self.window = None
            if self._held:
                self.release()
                self._held = False
        return False


def main_gui():
    app = NotifyApplication()
    return app.run(None)

import os
import subprocess
import sys
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango

from . import config, player, sounds

INHERITED = "__inherited__"
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
            default_width=580, default_height=680,
        )
        self.cfg = config.load_config()
        self.theme_ids = sorted(sounds.list_sounds().keys())
        if not self.theme_ids:
            self.theme_ids = ["message"]
        self.app_rows = {}
        self.custom_rows = {}
        self._rebuilding = False
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
        root.append(self.custom_box)

        self.no_dup_check = Gtk.CheckButton(
            label="No repetir si la app ya envía su propio sonido "
            "(desmarcado: se oirán ambos)"
        )
        self.no_dup_check.set_active(bool(self.cfg.get("no_duplicate", True)))
        self.no_dup_check.connect("toggled", self._on_no_dup_toggled)
        root.append(self.no_dup_check)

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
        apps_header_row.append(apps_header)
        apps_header_row.append(self.reset_apps_button)
        root.append(apps_header_row)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.apps_list = Gtk.ListBox()
        self.apps_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self.apps_list)
        root.append(scroll)

        self.state_label = Gtk.Label(label="", xalign=0, wrap=True)
        root.append(self.state_label)

        daemon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.start_button = Gtk.Button(label="Iniciar daemon")
        self.start_button.connect("clicked", self._on_start_daemon)
        self.stop_button = Gtk.Button(label="Detener daemon")
        self.stop_button.connect("clicked", self._on_stop_daemon)
        daemon_row.append(self.start_button)
        daemon_row.append(self.stop_button)
        daemon_row.append(Gtk.Label(label="", hexpand=True))
        root.append(daemon_row)

        self._populate_apps()
        self._refresh_custom_list()
        self._rebuild_all_dropdowns()

    def _populate_apps(self):
        seen = config.load_state().get("apps_seen", [])
        for app_name in seen:
            self._ensure_app_row(app_name)
        self._refresh_app_sensitivity()

    def _ensure_app_row(self, app_name):
        if app_name in self.app_rows:
            return
        app_cfg = self.cfg.get("apps", {}).get(app_name, {})
        row = Gtk.ListBoxRow()
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
            margin_top=4, margin_bottom=4,
        )
        name_label = Gtk.Label(
            label=self._display_name(app_name),
            hexpand=True, xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            tooltip_text=app_name,
        )
        name_label.set_max_width_chars(40)
        app_sound_dropdown = Gtk.DropDown()
        app_sound_dropdown.props.valign = Gtk.Align.CENTER
        app_sound_dropdown.connect(
            "notify::selected", self._on_app_sound_changed, app_name
        )
        app_test_button = Gtk.Button(label="Probar")
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
        app_switch = Gtk.Switch(active=bool(app_cfg.get("enabled", True)))
        app_switch.props.valign = Gtk.Align.CENTER
        app_switch.connect("notify::active", self._on_app_toggled, app_name)
        remove_button = Gtk.Button()
        remove_button.set_icon_name("user-trash-symbolic")
        remove_button.set_tooltip_text("Eliminar de la lista")
        remove_button.props.valign = Gtk.Align.CENTER
        remove_button.connect("clicked", self._on_app_remove, app_name)
        box.append(name_label)
        box.append(app_sound_dropdown)
        box.append(app_test_button)
        box.append(rename_button)
        box.append(info_button)
        box.append(app_switch)
        box.append(remove_button)
        row.set_child(box)
        self.apps_list.append(row)
        self.app_rows[app_name] = {
            "row": row,
            "switch": app_switch,
            "dropdown": app_sound_dropdown,
            "name_label": name_label,
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

    def _refresh_state(self):
        running = config.is_running()
        state = config.load_state()
        for app_name in state.get("apps_seen", []):
            self._ensure_app_row(app_name)
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

    def _on_no_dup_toggled(self, check):
        self.cfg["no_duplicate"] = check.get_active()
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

    def _on_test_app(self, button, app_name):
        app_cfg = self.cfg.get("apps", {}).get(app_name, {})
        choice = app_cfg.get("sound") or self.cfg.get("sound")
        if choice:
            player.play_choice(choice)

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

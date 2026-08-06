import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk

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

        apps_header = Gtk.Label(label="Aplicaciones", xalign=0, hexpand=True)
        apps_header.add_css_class("heading")
        root.append(apps_header)

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
        name_label = Gtk.Label(label=app_name, hexpand=True, xalign=0)
        app_sound_dropdown = Gtk.DropDown()
        app_sound_dropdown.connect(
            "notify::selected", self._on_app_sound_changed, app_name
        )
        app_test_button = Gtk.Button(label="Probar")
        app_test_button.connect("clicked", self._on_test_app, app_name)
        app_switch = Gtk.Switch(active=bool(app_cfg.get("enabled", True)))
        app_switch.connect("notify::active", self._on_app_toggled, app_name)
        box.append(name_label)
        box.append(app_sound_dropdown)
        box.append(app_test_button)
        box.append(app_switch)
        row.set_child(box)
        self.apps_list.append(row)
        self.app_rows[app_name] = {
            "row": row,
            "switch": app_switch,
            "dropdown": app_sound_dropdown,
        }

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
            )
            remove_button = Gtk.Button(label="Quitar")
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
        super().__init__(
            application_id="dev.notifysound.NotifySound",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )

    def do_activate(self):
        window = NotifyWindow(self)
        window.present()
        self.hold()


def main_gui():
    app = NotifyApplication()
    return app.run(None)

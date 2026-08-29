import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from notify_sound import cli, config, daemon, notify, player, sounds


ROOT = Path(__file__).resolve().parents[1]


def _hint_entry(hint):
    if isinstance(hint, tuple):
        key, value = hint
    else:
        key, value = hint, "message"
    if isinstance(value, int) and not isinstance(value, bool):
        # Formato real de dbus-monitor: alinea los tipos a una columna
        # fija, dejando varios espacios entre `variant` y `byte`
        # (p. ej. `variant             byte 2`).
        value_line = f"         variant             byte {value}\n"
    else:
        value_line = f'         variant string "{value}"\n'
    return (
        "      dict entry(\n"
        f'         string "{key}"\n'
        f"{value_line}"
        "      )\n"
    )


def _lines(payload):
    """Convierte un payload de notificación (bytes) en líneas para el parser."""
    return payload.decode("utf-8").splitlines()


def notification(
    app_name, hints=(), trailing_blank=True, body="Body", summary="Summary"
):
    hint_lines = "".join(_hint_entry(hint) for hint in hints)
    payload = "".join(
        [
            "method call time=1 sender=:1.1 -> "
            "destination=org.freedesktop.Notifications serial=1 "
            "path=/org/freedesktop/Notifications; "
            "interface=org.freedesktop.Notifications; member=Notify\n",
            f'   string "{app_name}"\n',
            "   uint32 0\n",
            '   string ""\n',
            f'   string "{summary}"\n',
            f'   string "{body}"\n',
            "   array [\n",
            "   ]\n",
            "   array [\n",
            hint_lines,
            "   ]\n",
            "   int32 -1\n",
        ]
    )
    return (payload + ("\n" if trailing_blank else "")).encode()


def gtk_notification(
    app_id="org.gnome.Ptyxis",
    hints=(),
    trailing_blank=True,
    title="Comando completado",
    body="sleep 5",
):
    entries = [
        ("title", title),
        ("body", body),
    ] + [hint if isinstance(hint, tuple) else (hint, "message") for hint in hints]
    notification_entries = "".join(
        "      dict entry(\n"
        f'         string "{key}"\n'
        f'         variant string "{value}"\n'
        "      )\n"
        for key, value in entries
    )
    payload = "".join(
        [
            "method call time=1 sender=:1.1 -> "
            "destination=org.gtk.Notifications serial=1 "
            "path=/org/gtk/Notifications; "
            "interface=org.gtk.Notifications; member=AddNotification\n",
            f'   string "{app_id}"\n',
            '   string "notification-id"\n',
            "   array [\n",
            notification_entries,
            "   ]\n",
        ]
    )
    return (payload + ("\n" if trailing_blank else "")).encode()


def _bare_window(cfg):
    from notify_sound import gui

    window = gui.NotifyWindow.__new__(gui.NotifyWindow)
    window.cfg = cfg
    window.app_rows = {}
    window.apps_list = mock.Mock()
    window._rebuilding = False
    return window


class FakeLoop:
    def __init__(self, running=False):
        self.running = running

    def is_running(self):
        return self.running

    def quit(self):
        self.running = False


class FakeMonitor:
    def __init__(self, payload):
        self.stdout = io.BytesIO(payload)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        return None

    def kill(self):
        return None


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config_dir = root / "config" / "notify-sound"
        self.runtime_dir = root / "runtime"
        self.config_dir.mkdir(parents=True)
        self.runtime_dir.mkdir()
        self.originals = {
            "CONFIG_DIR": config.CONFIG_DIR,
            "CONFIG_FILE": config.CONFIG_FILE,
            "STATE_FILE": config.STATE_FILE,
            "PID_FILE": config.PID_FILE,
            "AUTOSTART_DIR": config.AUTOSTART_DIR,
            "AUTOSTART_FILE": config.AUTOSTART_FILE,
        }
        config.CONFIG_DIR = str(self.config_dir)
        config.CONFIG_FILE = str(self.config_dir / "config.json")
        config.STATE_FILE = str(self.config_dir / "state.json")
        config.PID_FILE = str(self.runtime_dir / "notify-sound.pid")
        config.AUTOSTART_DIR = str(root / "config" / "autostart")
        config.AUTOSTART_FILE = str(
            Path(config.AUTOSTART_DIR) / "notify-sound.desktop"
        )

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(config, name, value)
        self.temp.cleanup()

    def write_config(self, data):
        Path(config.CONFIG_FILE).write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_corrupt_config_is_safe_for_shared_gui_daemon_loader(self):
        cases = [
            "{",
            "null",
            "[]",
            {
                "enabled": None,
                "sound": None,
                "custom_sounds": None,
                "autostart": None,
                "apps": [],
            },
            {"apps": {"warp": "x", "Telegram Desktop": None}},
        ]
        for case in cases:
            if isinstance(case, str):
                Path(config.CONFIG_FILE).write_text(case, encoding="utf-8")
            else:
                self.write_config(case)
            loaded = config.load_config()
            self.assertIsInstance(loaded["apps"], dict)
            self.assertIsInstance(loaded["enabled"], bool)
            self.assertIsInstance(loaded["autostart"], bool)
            self.assertIsInstance(loaded["custom_sounds"], list)
            self.assertIsInstance(loaded["sound"], str)
            if "warp" in loaded["apps"]:
                self.assertTrue(loaded["apps"]["warp"]["enabled"])
                self.assertIsNone(loaded["apps"]["warp"]["sound"])

        from notify_sound import gui

        self.assertIs(gui.config, config)
        self.assertIsInstance(gui.config.load_config(), dict)
        self.assertIsInstance(daemon.NotifyDaemon().seen, set)

    def test_legacy_custom_sound_migration_is_preserved(self):
        legacy = "/tmp/legacy.wav"
        self.write_config({"custom_sound": legacy})
        loaded = config.load_config()
        self.assertEqual(loaded["sound"], legacy)
        self.assertEqual(loaded["custom_sounds"], [legacy])

    def test_legacy_no_duplicate_field_is_ignored(self):
        # OWN-001: el campo global no_duplicate ya no se usa. Un config
        # existente que lo traiga se carga sin él, sin romper ni
        # re-persistirlo.
        self.write_config({"no_duplicate": False, "enabled": True})
        loaded = config.load_config()
        self.assertNotIn("no_duplicate", loaded)
        self.assertTrue(loaded["enabled"])

    def test_normalize_app_rejects_invalid_alias_and_keeps_valid(self):
        variants = [
            {"enabled": True, "sound": None, "name": None},
            {"enabled": True, "sound": None, "name": ""},
            {"enabled": True, "sound": None, "name": 12},
            {"enabled": True, "sound": None, "name": "x" * (config.MAX_APP_NAME_LENGTH + 1)},
        ]
        for variant in variants:
            self.write_config({"apps": {"aimp": variant}})
            loaded = config.load_config()
            self.assertNotIn("name", loaded["apps"]["aimp"], msg=str(variant))

        self.write_config(
            {"apps": {"aimp": {"enabled": True, "sound": None, "name": "AIMP"}}}
        )
        self.assertEqual(config.load_config()["apps"]["aimp"]["name"], "AIMP")

    def test_current_style_config_round_trips_custom_sounds_and_apps(self):
        current = {
            "enabled": True,
            "sound": "alarm-clock-elapsed",
            "custom_sounds": [
                "/tmp/notify-sound-test-I-Feel-Good.wav",
                "/tmp/notify-sound-test-Whistle.wav",
            ],
            "autostart": True,
            "debounce_window": 2.0,
            "apps": {
                "warp": {
                    "enabled": True,
                    "sound": "/tmp/notify-sound-test-I-Feel-Good.wav",
                    "volume": 100,
                },
                "Telegram Desktop": {
                    "enabled": True,
                    "sound": "/tmp/notify-sound-test-Whistle.wav",
                    "volume": 100,
                },
            },
        }
        self.write_config(current)
        self.assertEqual(config.load_config(), current)

    def test_registered_state_keeps_existing_apps(self):
        state = {
            "apps_seen": ["Telegram Desktop", "notify-send", "warp"],
            "app_meta": {"warp": {"seen_count": 3, "comm": "warp"}},
        }
        Path(config.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(config.load_state(), state)

    def test_load_state_keeps_has_own_sound_bool_and_drops_invalid(self):
        # OWN-001: load_state valida has_own_sound (bool). Ausente (legacy),
        # invalido (str) -> se omite, default false implicito; bool valido
        # (True o False) -> se conserva.
        state = {
            "apps_seen": ["warp", "telegram", "vlc", "legacy"],
            "app_meta": {
                "warp": {"seen_count": 1, "has_own_sound": True},
                "telegram": {"seen_count": 1, "has_own_sound": "si"},
                "vlc": {"seen_count": 1, "has_own_sound": False},
                "legacy": {"seen_count": 1},
            },
        }
        Path(config.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        loaded = config.load_state()
        self.assertIs(loaded["app_meta"]["warp"]["has_own_sound"], True)
        self.assertIs(loaded["app_meta"]["vlc"]["has_own_sound"], False)
        self.assertNotIn("has_own_sound", loaded["app_meta"]["telegram"])
        self.assertNotIn("has_own_sound", loaded["app_meta"]["legacy"])

    def test_save_state_round_trips_has_own_sound(self):
        # OWN-001: save_state persiste has_own_sound solo si es bool valido.
        config.save_state(
            {
                "apps_seen": ["warp", "vlc", "telegram"],
                "app_meta": {
                    "warp": {"seen_count": 2, "has_own_sound": True},
                    "vlc": {"seen_count": 2, "has_own_sound": False},
                    "telegram": {"seen_count": 2, "has_own_sound": "si"},
                },
            }
        )
        loaded = config.load_state()
        self.assertIs(loaded["app_meta"]["warp"]["has_own_sound"], True)
        self.assertIs(loaded["app_meta"]["vlc"]["has_own_sound"], False)
        self.assertNotIn("has_own_sound", loaded["app_meta"]["telegram"])

    def test_find_alias_owner_resolves_key_or_name(self):
        cfg = {
            "apps": {
                "aimp": {"enabled": True, "sound": None, "name": "AIMP"},
                "warp": {"enabled": True, "sound": None},
            }
        }
        self.assertEqual(config._find_alias_owner(cfg, "AIMP"), "aimp")
        self.assertEqual(config._find_alias_owner(cfg, "warp"), "warp")
        self.assertIsNone(config._find_alias_owner(cfg, "unknown"))

    def test_find_alias_owner_returns_none_when_ambiguous(self):
        cfg = {
            "apps": {
                "aimp1": {"name": "AIMP"},
                "aimp2": {"name": "AIMP"},
            }
        }
        self.assertIsNone(config._find_alias_owner(cfg, "AIMP"))

    def test_config_migration_merges_duplicate_aliases_and_moves_to_synonyms(self):
        self.write_config(
            {
                "apps": {
                    "songA": {
                        "enabled": True, "sound": None, "name": "AIMP",
                    },
                    "songB": {
                        "enabled": False,
                        "sound": "/tmp/x.wav",
                        "name": "AIMP",
                    },
                }
            }
        )
        Path(config.STATE_FILE).write_text(
            json.dumps({"apps_seen": ["songA", "songB"]}),
            encoding="utf-8",
        )
        config.load_config()
        merged = config.load_config()
        apps = merged["apps"]
        self.assertEqual(set(apps), {"songA"})
        survivor = apps["songA"]
        self.assertEqual(survivor["name"], "AIMP")
        self.assertEqual(survivor["enabled"], False)
        self.assertEqual(survivor["sound"], "/tmp/x.wav")
        self.assertIn("songB", survivor.get("synonyms", []))
        state = config.load_state()
        self.assertIn("songA", state["apps_seen"])
        self.assertNotIn("songB", state["apps_seen"])

    def test_config_migration_drops_state_synonyms_from_apps_seen(self):
        self.write_config(
            {
                "apps": {
                    "aimp": {
                        "enabled": True, "sound": None, "name": "AIMP",
                        "synonyms": ["Canción antigua"],
                    }
                }
            }
        )
        Path(config.STATE_FILE).write_text(
            json.dumps(
                {"apps_seen": ["aimp", "Canción antigua", "warp"]}
            ),
            encoding="utf-8",
        )
        config.load_config()
        self.assertEqual(
            config.load_state()["apps_seen"], ["aimp", "warp"]
        )

    def test_normalize_app_rejects_synonyms_duplicates_and_bounds(self):
        long_value = "x" * (config.MAX_APP_NAME_LENGTH + 1)
        variant = {
            "enabled": True,
            "sound": None,
            "synonyms": ["a", "a", "b", "", long_value]
            + [f"s{i}" for i in range(config.MAX_SYNONYMS)],
        }
        self.write_config({"apps": {"aimp": variant}})
        loaded = config.load_config()
        synonyms = loaded["apps"]["aimp"]["synonyms"]
        self.assertEqual(len(synonyms), config.MAX_SYNONYMS)
        self.assertEqual(len(set(synonyms)), len(synonyms))
        self.assertNotIn("", synonyms)
        self.assertNotIn(long_value, synonyms)

    def test_normalize_app_defaults_volume_to_100_when_missing(self):
        self.write_config({"apps": {"aimp": {"enabled": True, "sound": None}}})
        loaded = config.load_config()
        self.assertEqual(loaded["apps"]["aimp"]["volume"], 100)

    def test_normalize_app_keeps_valid_volume_values(self):
        for value in (0, 50, 100):
            self.write_config(
                {"apps": {"aimp": {"enabled": True, "volume": value}}}
            )
            loaded = config.load_config()
            self.assertEqual(
                loaded["apps"]["aimp"]["volume"], value, msg=str(value)
            )

    def test_normalize_app_rejects_invalid_volume_values(self):
        invalid = ["50", 50.0, None, True, False, [], -1, 101]
        for value in invalid:
            self.write_config(
                {"apps": {"aimp": {"enabled": True, "volume": value}}}
            )
            loaded = config.load_config()
            self.assertEqual(
                loaded["apps"]["aimp"]["volume"], 100, msg=str(value)
            )

    def test_debounce_window_default_when_absent(self):
        self.write_config({"enabled": True, "sound": "message"})
        loaded = config.load_config()
        self.assertEqual(loaded["debounce_window"], 2.0)

    def test_debounce_window_valid_values_are_loaded(self):
        for value, expected in ((5.0, 5.0), (0, 0.0), (0.5, 0.5), (10, 10.0)):
            self.write_config({"debounce_window": value})
            loaded = config.load_config()
            self.assertEqual(
                loaded["debounce_window"], expected, msg=str(value)
            )
            self.assertIsInstance(loaded["debounce_window"], float)

    def test_debounce_window_invalid_values_use_default(self):
        invalid = [-1.0, "2.0", None, True, False, [], {}]
        for value in invalid:
            self.write_config({"debounce_window": value})
            loaded = config.load_config()
            self.assertEqual(
                loaded["debounce_window"], 2.0, msg=str(value)
            )

    def test_json_files_are_private_and_state_is_bounded(self):
        config.save_config(config.DEFAULT_CONFIG)
        config.save_state(
            {
                "apps_seen": [
                    f"app-{index}"
                    for index in range(config.MAX_STATE_APPS + 10)
                ]
            }
        )
        self.assertEqual(config.CONFIG_FILE, str(self.config_dir / "config.json"))
        self.assertEqual(
            Path(config.CONFIG_FILE).stat().st_mode & 0o777,
            config.PRIVATE_FILE_MODE,
        )
        self.assertEqual(
            Path(config.STATE_FILE).stat().st_mode & 0o777,
            config.PRIVATE_FILE_MODE,
        )
        self.assertEqual(
            self.config_dir.stat().st_mode & 0o777,
            config.PRIVATE_DIR_MODE,
        )
        self.assertEqual(
            len(config.load_state()["apps_seen"]), config.MAX_STATE_APPS
        )

    def test_autostart_rejects_control_characters_in_binary_path(self):
        with mock.patch.dict(
            os.environ,
            {"NOTIFY_SOUND_BIN": "/tmp/notify-sound\nattacker"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                config.set_autostart(True)

    def test_autostart_uses_installed_binary_from_environment(self):
        with mock.patch.dict(
            os.environ,
            {"NOTIFY_SOUND_BIN": "/opt/test/bin/notify-sound"},
            clear=False,
        ):
            config.set_autostart(True)
        desktop = Path(config.AUTOSTART_FILE).read_text(encoding="utf-8")
        self.assertIn(
            'Exec="/opt/test/bin/notify-sound" --daemon', desktop
        )

    def test_instance_lock_rejects_second_holder(self):
        first = config.acquire_instance_lock()
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(config.acquire_instance_lock())
        finally:
            first.close()
            config.remove_pid()

    def test_normalize_rules_valid(self):
        # RULE-001: regla valida con contains se guarda normalizada.
        rules = [
            {
                "match": {
                    "field": "body",
                    "op": "contains",
                    "value": "esperando permiso",
                },
                "action": "sound",
                "sound": "/tmp/rule.wav",
            }
        ]
        self.assertEqual(
            config._normalize_rules(rules),
            [
                {
                    "match": {
                        "field": "body",
                        "op": "contains",
                        "value": "esperando permiso",
                    },
                    "action": "sound",
                    "sound": "/tmp/rule.wav",
                }
            ],
        )

    def test_normalize_rules_regex_valid(self):
        # RULE-001: regex compilable se guarda tal cual.
        rules = [
            {
                "match": {
                    "field": "summary",
                    "op": "regex",
                    "value": r"^Comando .* listo$",
                },
                "action": "sound",
                "sound": "message",
            }
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["match"]["op"], "regex")
        self.assertEqual(normalized[0]["match"]["value"], r"^Comando .* listo$")
        self.assertEqual(normalized[0]["sound"], "message")

    def test_normalize_rules_regex_invalid_discarded(self):
        # RULE-001: regex invalida se descarta sin romper el resto.
        rules = [
            {
                "match": {"field": "body", "op": "regex", "value": "[unclosed"},
                "action": "sound",
                "sound": "message",
            },
            {
                "match": {"field": "body", "op": "contains", "value": "ok"},
                "action": "sound",
                "sound": "message",
            },
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["match"]["op"], "contains")

    def test_normalize_rules_eq_int(self):
        # RULE-001: eq con int (p. ej. urgency) se guarda.
        rules = [
            {
                "match": {"field": "urgency", "op": "eq", "value": 2},
                "action": "sound",
                "sound": "critical",
            }
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["match"]["value"], 2)

    def test_normalize_rules_eq_bool_discarded(self):
        # RULE-001: bool es subclase de int y se descarta como value de eq.
        for value in (True, False):
            rules = [
                {
                    "match": {"field": "urgency", "op": "eq", "value": value},
                    "action": "sound",
                    "sound": "critical",
                }
            ]
            self.assertEqual(
                config._normalize_rules(rules), [], msg=str(value)
            )

    def test_normalize_rules_starts_with_valid(self):
        # RULE-001: regla con op starts_with y value string se guarda.
        rules = [
            {
                "match": {
                    "field": "body",
                    "op": "starts_with",
                    "value": "Latest",
                },
                "action": "sound",
                "sound": "custom",
            }
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["match"]["op"], "starts_with")
        self.assertEqual(normalized[0]["match"]["value"], "Latest")

    def test_normalize_rules_ends_with_valid(self):
        # RULE-001: regla con op ends_with y value string se guarda.
        rules = [
            {
                "match": {
                    "field": "summary",
                    "op": "ends_with",
                    "value": "finished",
                },
                "action": "sound",
                "sound": "custom",
            }
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["match"]["op"], "ends_with")
        self.assertEqual(normalized[0]["match"]["value"], "finished")

    def test_normalize_rules_starts_with_invalid_op_discarded(self):
        # RULE-001: op parecido pero invalido (startswith sin underscore)
        # se descarta; solo los ops de _RULE_OPS son validos.
        rules = [
            {
                "match": {"field": "body", "op": "startswith", "value": "x"},
                "action": "sound",
                "sound": "custom",
            }
        ]
        self.assertEqual(config._normalize_rules(rules), [])

    def test_normalize_rules_silence_no_sound(self):
        # RULE-001: action silence no requiere sound y no lo guarda.
        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": "spam"},
                "action": "silence",
            }
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["action"], "silence")
        self.assertNotIn("sound", normalized[0])

    def test_normalize_rules_sound_without_sound_discarded(self):
        # RULE-001: action sound sin sound valido (ausente, None o vacio)
        # descarta la regla.
        variants = [
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
            },
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": None,
            },
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": "",
            },
        ]
        for rule in variants:
            self.assertEqual(
                config._normalize_rules([rule]), [], msg=str(rule)
            )

    def test_normalize_rules_malformed_discarded(self):
        # RULE-001: reglas malformadas se descartan sin romper el resto.
        long_value = "x" * (config.MAX_PATH_LENGTH + 1)
        rules = [
            "not-a-dict",
            None,
            {"match": "not-a-dict", "action": "sound", "sound": "x"},
            {
                "match": {"field": "", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": "x",
            },
            {
                "match": {"field": "body", "op": "bogus", "value": "x"},
                "action": "sound",
                "sound": "x",
            },
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "bogus",
            },
            {
                "match": {"field": "body", "op": "contains", "value": ""},
                "action": "sound",
                "sound": "x",
            },
            {
                "match": {"field": "body", "op": "contains", "value": long_value},
                "action": "sound",
                "sound": "x",
            },
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": long_value,
            },
        ]
        self.assertEqual(config._normalize_rules(rules), [])

    def test_normalize_rules_max_rules(self):
        # RULE-001: el numero de reglas validas se acota a MAX_RULES.
        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": f"v{i}"},
                "action": "sound",
                "sound": "message",
            }
            for i in range(config.MAX_RULES + 10)
        ]
        normalized = config._normalize_rules(rules)
        self.assertEqual(len(normalized), config.MAX_RULES)

    def test_normalize_app_with_rules(self):
        # RULE-001: _normalize_app conserva solo las reglas validas.
        app = {
            "enabled": True,
            "sound": None,
            "rules": [
                {
                    "match": {
                        "field": "body",
                        "op": "contains",
                        "value": "esperando permiso",
                    },
                    "action": "sound",
                    "sound": "/tmp/rule.wav",
                },
                {
                    "match": {"field": "body", "op": "regex", "value": "[unclosed"},
                    "action": "sound",
                    "sound": "x",
                },
                {
                    "match": {"field": "body", "op": "contains", "value": "spam"},
                    "action": "silence",
                },
            ],
        }
        normalized = config._normalize_app(app)
        self.assertEqual(len(normalized["rules"]), 2)
        self.assertEqual(normalized["rules"][0]["action"], "sound")
        self.assertEqual(normalized["rules"][0]["sound"], "/tmp/rule.wav")
        self.assertEqual(normalized["rules"][1]["action"], "silence")
        self.assertNotIn("sound", normalized["rules"][1])

    def test_normalize_app_empty_rules_dropped(self):
        # RULE-001: lista de reglas vacia o invalida -> el campo rules no
        # aparece en el resultado (mismo criterio que synonyms).
        variants = [
            [],
            None,
            "not-a-list",
            [
                {
                    "match": {"field": "body", "op": "contains", "value": "x"},
                    "action": "bogus",
                }
            ],
        ]
        for rules in variants:
            normalized = config._normalize_app(
                {"enabled": True, "rules": rules}
            )
            self.assertNotIn("rules", normalized, msg=str(rules))

    def test_load_config_persists_rules(self):
        # RULE-001: load_config carga reglas validadas desde config.json.
        self.write_config(
            {
                "apps": {
                    "opencode": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando permiso",
                                },
                                "action": "sound",
                                "sound": "/tmp/rule.wav",
                            },
                            {
                                "match": {
                                    "field": "body",
                                    "op": "regex",
                                    "value": "[unclosed",
                                },
                                "action": "sound",
                                "sound": "x",
                            },
                        ],
                    }
                }
            }
        )
        loaded = config.load_config()
        rules = loaded["apps"]["opencode"]["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["match"]["value"], "esperando permiso")
        self.assertEqual(rules[0]["sound"], "/tmp/rule.wav")


class SoundTests(unittest.TestCase):
    def test_theme_flac_is_listed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stereo = root / "sounds" / "test-theme" / "stereo"
            stereo.mkdir(parents=True)
            flac = stereo / "theme-tone.flac"
            flac.write_bytes(b"not-a-real-audio-file")
            with mock.patch.object(
                sounds, "theme_name", return_value="test-theme"
            ), mock.patch.dict(
                os.environ, {"XDG_DATA_DIRS": str(root)}, clear=False
            ):
                available = sounds.list_sounds()
        self.assertEqual(available["theme-tone"], str(flac))


class PlayerTests(unittest.TestCase):
    def _audio_file(self, suffix=".mp3"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / f"sound{suffix}"
        path.write_bytes(b"not-a-real-audio-file")
        return str(path)

    def test_play_sound_applies_canberra_volume_flag(self):
        with mock.patch.object(player.subprocess, "Popen") as popen:
            self.assertTrue(player.play_sound("message", volume=50))
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "canberra-gtk-play")
        self.assertIn("--volume=-6.02", command)

    def test_play_sound_passes_explicit_volume_flag_at_100(self):
        with mock.patch.object(player.subprocess, "Popen") as popen:
            self.assertTrue(player.play_sound("message", volume=100))
        command = popen.call_args.args[0]
        self.assertEqual(
            command,
            ["canberra-gtk-play", "-i", "message", "--volume=0.0"],
        )

    def test_play_sound_invalid_volume_uses_explicit_100_volume_flag(self):
        for value in ("50", 50.0, None, True, -1, 101):
            with mock.patch.object(player.subprocess, "Popen") as popen:
                self.assertTrue(player.play_sound("message", volume=value))
            self.assertEqual(
                popen.call_args.args[0],
                ["canberra-gtk-play", "-i", "message", "--volume=0.0"],
                msg=str(value),
            )

    def test_play_sound_does_not_launch_at_volume_zero(self):
        with mock.patch.object(player.subprocess, "Popen") as popen:
            self.assertFalse(player.play_sound("message", volume=0))
        popen.assert_not_called()

    def test_play_file_canberra_extension_applies_volume_flag(self):
        path = self._audio_file(".wav")
        with mock.patch.object(player.subprocess, "Popen") as popen:
            self.assertTrue(player.play_file(path, volume=50))
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "canberra-gtk-play")
        self.assertIn("--volume=-6.02", command)

    def test_play_file_fallback_applies_volume_flags(self):
        path = self._audio_file(".mp3")
        calls = []
        event = threading.Event()

        def fake_popen(command, **kwargs):
            calls.append(command)
            if len(calls) == 4:
                event.set()
            proc = mock.Mock()
            proc.wait.return_value = 1
            return proc

        with mock.patch.object(
            player.subprocess, "Popen", side_effect=fake_popen
        ):
            self.assertTrue(player.play_file(path, volume=50))
        self.assertTrue(event.wait(1))
        self.assertEqual(len(calls), 4)
        gst, ffplay, mpv, mpg123 = calls
        self.assertIn("volume=0.50", gst)
        self.assertEqual(ffplay[ffplay.index("-volume") + 1], "50")
        self.assertIn("--volume=50", mpv)
        self.assertEqual(mpg123[mpg123.index("-f") + 1], "16384")

    def test_play_file_fallback_passes_explicit_volume_flags_at_100(self):
        path = self._audio_file(".mp3")
        calls = []
        event = threading.Event()

        def fake_popen(command, **kwargs):
            calls.append(command)
            if len(calls) == 4:
                event.set()
            proc = mock.Mock()
            proc.wait.return_value = 1
            return proc

        with mock.patch.object(
            player.subprocess, "Popen", side_effect=fake_popen
        ):
            self.assertTrue(player.play_file(path, volume=100))
        self.assertTrue(event.wait(1))
        self.assertEqual(len(calls), 4)
        gst, ffplay, mpv, mpg123 = calls
        self.assertIn("volume=1.0", gst)
        self.assertEqual(ffplay[ffplay.index("-volume") + 1], "100")
        self.assertIn("--volume=100", mpv)
        self.assertEqual(mpg123[mpg123.index("-f") + 1], "32768")

    def test_play_file_does_not_launch_at_volume_zero(self):
        path = self._audio_file(".mp3")
        with mock.patch.object(player.subprocess, "Popen") as popen:
            self.assertFalse(player.play_file(path, volume=0))
        popen.assert_not_called()

    def test_play_choice_propagates_volume(self):
        path = self._audio_file(".mp3")
        with mock.patch.object(player, "play_sound") as play_sound, \
             mock.patch.object(player, "play_file") as play_file:
            player.play_choice("message", volume=30)
            player.play_choice(path, volume=30)
        play_sound.assert_called_once_with("message", volume=30)
        play_file.assert_called_once_with(path, volume=30)


class DaemonTests(ConfigTests):
    def make_daemon(self, payload, resolve_sender=False):
        daemon._sender_cache.clear()
        if not resolve_sender:
            patcher = mock.patch.object(
                daemon, "_resolve_sender_to_comm", return_value=None
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        instance = daemon.NotifyDaemon()
        instance.loop = FakeLoop()
        instance.accept_restarts = False
        monitor = FakeMonitor(payload)
        instance.monitor = monitor
        return instance, monitor

    def test_single_notification_is_played_immediately_at_eof(self):
        instance, monitor = self.make_daemon(
            notification("notify-send", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound", "notify-send"})

    def test_two_notifications_are_both_processed(self):
        payload = notification("first") + notification(
            "second", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        self.assertEqual(play.call_count, 2)
        self.assertEqual(instance.seen, {"notify-sound", "first", "second"})

    def test_sound_hints_are_not_duplicated(self):
        for hint in ("sound-name", "sound-file"):
            instance, monitor = self.make_daemon(
                notification("with-hint", hints=(hint,), trailing_blank=False)
            )
            with mock.patch.object(player, "play_choice") as play:
                instance._reader(monitor)
            play.assert_not_called()

    def test_own_sound_without_app_config_is_not_played(self):
        # OWN-001: app con sonido propio sin entrada en config.apps no se
        # reproduce (evita duplicar el sonido que ya envía la app).
        instance, monitor = self.make_daemon(
            notification("with-hint", hints=("sound-name",), trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_own_sound_with_app_config_is_played(self):
        # OWN-001: si el usuario configuró la app (entrada en config.apps),
        # se respeta su elección aunque la app traiga sonido propio.
        self.write_config(
            {"sound": "message", "apps": {"with-hint": {"enabled": True}}}
        )
        instance, monitor = self.make_daemon(
            notification("with-hint", hints=("sound-name",), trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_multiline_body_processes_once_and_skips_shell_reemission(self):
        body = "web.whatsapp.com\n\nV"
        payload = notification("Vivaldi", body=body) + notification(
            "Vivaldi",
            hints=("x-shell-sender",),
            body=body,
            trailing_blank=False,
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_string_content_cannot_fake_message_terminator(self):
        instance, monitor = self.make_daemon(
            notification(
                "Vivaldi",
                hints=("suppress-sound",),
                body="before\n   int32 0\nmethod call fake",
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_incomplete_message_at_eof_is_discarded(self):
        payload = notification("incomplete", trailing_blank=False)
        payload = payload.rsplit(b"   int32 -1\n", 1)[0]
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_gtk_notification_is_played_and_registered(self):
        instance, monitor = self.make_daemon(
            gtk_notification(trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound", "org.gnome.Ptyxis"})

    def test_gtk_notification_respects_suppress_sound(self):
        instance, monitor = self.make_daemon(
            gtk_notification(
                hints=("suppress-sound",), trailing_blank=False
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_desktop_entry_hint_overrides_dynamic_app_name(self):
        instance, monitor = self.make_daemon(
            notification(
                "Pink Floyd - Time",
                hints=(("desktop-entry", "aimp"),),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound", "aimp"})

    def test_desktop_entry_hint_is_ignored_when_empty_or_too_long(self):
        long_value = "x" * (config.MAX_APP_NAME_LENGTH + 1)

        instance, monitor = self.make_daemon(
            notification(
                "short",
                hints=(("desktop-entry", ""),),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertEqual(instance.seen, {"notify-sound", "short"})

        instance, monitor = self.make_daemon(
            notification(
                "short",
                hints=(("desktop-entry", long_value),),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertEqual(instance.seen, {"notify-sound", "short"})

    def test_desktop_entry_hint_keeps_x_shell_sender_and_sound_name_rules(self):
        payload = (
            notification(
                "Some Song Title",
                hints=(("desktop-entry", "aimp"), "sound-name"),
                trailing_blank=False,
            )
            + notification(
                "Some Song Title",
                hints=(("desktop-entry", "aimp"), "x-shell-sender"),
                trailing_blank=False,
            )
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()
        self.assertEqual(instance.seen, {"notify-sound", "aimp"})

    def test_parse_block_captures_urgency_byte(self):
        payload = notification(
            "app", hints=(("urgency", 2),), trailing_blank=False
        )
        app_name, hints, desktop_entry, urgency, *_ = daemon._parse_block(
            _lines(payload)
        )
        self.assertEqual(urgency, 2)
        self.assertIn("urgency", hints)

    def test_parse_block_urgency_absent_is_none(self):
        payload = notification("app", trailing_blank=False)
        app_name, hints, desktop_entry, urgency, *_ = daemon._parse_block(
            _lines(payload)
        )
        self.assertIsNone(urgency)

    def test_parse_block_urgency_out_of_range_is_none(self):
        payload = notification(
            "app", hints=(("urgency", 5),), trailing_blank=False
        )
        app_name, hints, desktop_entry, urgency, *_ = daemon._parse_block(
            _lines(payload)
        )
        self.assertIsNone(urgency)

    def test_parse_block_urgency_low_and_normal(self):
        for value in (0, 1):
            payload = notification(
                "app", hints=(("urgency", value),), trailing_blank=False
            )
            app_name, hints, desktop_entry, urgency, *_ = daemon._parse_block(
                _lines(payload)
            )
            self.assertEqual(urgency, value, msg=str(value))

    def test_parse_block_urgency_real_dbus_monitor_format(self):
        # Formato real verificado empíricamente: dbus-monitor alinea los
        # tipos a una columna fija (varios espacios entre `variant` y
        # `byte`) e intercala otros tipos de hint (p. ej. `int64`).
        payload = (
            "method call time=1 sender=:1.1445 -> "
            "destination=:1.33 serial=1 path=/org/freedesktop/Notifications; "
            "interface=org.freedesktop.Notifications; member=Notify\n"
            '   string "notify-send"\n'
            "   uint32 0\n"
            '   string ""\n'
            '   string "Test"\n'
            '   string "Critica"\n'
            "   array [\n"
            "   ]\n"
            "   array [\n"
            "      dict entry(\n"
            '         string "urgency"\n'
            "         variant             byte 2\n"
            "      )\n"
"      dict entry(\n"
            '         string "urgency"\n'
            "         variant             byte 2\n"
            "      )\n"
            "   ]\n"
            "   int32 -1\n"
        ).encode()
        app_name, hints, desktop_entry, urgency, *_ = daemon._parse_block(
            _lines(payload)
        )
        self.assertEqual(urgency, 2)
        self.assertIn("urgency", hints)

    def test_parse_block_captures_summary_and_body(self):
        payload = notification(
            "app", body="Cuerpo distintivo", trailing_blank=False
        )
        app_name, hints, desktop_entry, urgency, summary, body = (
            daemon._parse_block(_lines(payload))
        )
        self.assertEqual(app_name, "app")
        self.assertEqual(summary, "Summary")
        self.assertEqual(body, "Cuerpo distintivo")

    def test_parse_block_body_multiline(self):
        # dbus-monitor escapa los saltos de línea reales como \\n dentro
        # de las comillas; el tokenizer los decodifica a \n literales.
        payload = (
            "method call time=1 sender=:1.1 -> "
            "destination=org.freedesktop.Notifications serial=1 "
            "path=/org/freedesktop/Notifications; "
            "interface=org.freedesktop.Notifications; member=Notify\n"
            '   string "app"\n'
            "   uint32 0\n"
            '   string ""\n'
            '   string "Summary"\n'
            '   string "línea1\\n\\nlínea2"\n'
            "   array [\n"
            "   ]\n"
            "   array [\n"
            "   ]\n"
            "   int32 -1\n"
        ).encode()
        app_name, hints, desktop_entry, urgency, summary, body = (
            daemon._parse_block(_lines(payload))
        )
        self.assertEqual(summary, "Summary")
        self.assertEqual(body, "línea1\n\nlínea2")

    def test_parse_block_summary_body_absent(self):
        # Payload malformado con menos strings top-level de los esperados:
        # summary y body quedan None sin romper el parseo.
        payload = (
            "method call time=1 sender=:1.1 -> "
            "destination=org.freedesktop.Notifications serial=1 "
            "path=/org/freedesktop/Notifications; "
            "interface=org.freedesktop.Notifications; member=Notify\n"
            '   string "app"\n'
            "   uint32 0\n"
            '   string ""\n'
            "   array [\n"
            "   ]\n"
            "   array [\n"
            "   ]\n"
            "   int32 -1\n"
        ).encode()
        app_name, hints, desktop_entry, urgency, summary, body = (
            daemon._parse_block(_lines(payload))
        )
        self.assertEqual(app_name, "app")
        self.assertIsNone(summary)
        self.assertIsNone(body)

    def test_parse_block_gtk_has_no_summary_body(self):
        # AddNotification (GTK) no tiene summary/body top-level: van
        # dentro del dict de hints. summary y body quedan None.
        payload = gtk_notification(trailing_blank=False)
        app_name, hints, desktop_entry, urgency, summary, body = (
            daemon._parse_block(_lines(payload))
        )
        self.assertEqual(app_name, "org.gnome.Ptyxis")
        self.assertIsNone(summary)
        self.assertIsNone(body)

    def test_parse_block_real_dbus_monitor_format_with_body(self):
        # Formato real de dbus-monitor (alineación de tipos a columna
        # fija) con summary y body reales, incluido un salto de línea
        # escapado en el cuerpo.
        payload = (
            "method call time=1 sender=:1.1445 -> "
            "destination=:1.33 serial=1 path=/org/freedesktop/Notifications; "
            "interface=org.freedesktop.Notifications; member=Notify\n"
            '   string "notify-send"\n'
            "   uint32 0\n"
            '   string ""\n'
            '   string "Título real"\n'
            '   string "Cuerpo real con \\n\\n salto"\n'
            "   array [\n"
            "   ]\n"
            "   array [\n"
            "      dict entry(\n"
            '         string "urgency"\n'
            "         variant             byte 2\n"
            "      )\n"
            "   ]\n"
            "   int32 -1\n"
        ).encode()
        app_name, hints, desktop_entry, urgency, summary, body = (
            daemon._parse_block(_lines(payload))
        )
        self.assertEqual(app_name, "notify-send")
        self.assertEqual(summary, "Título real")
        self.assertEqual(body, "Cuerpo real con \n\n salto")
        self.assertEqual(urgency, 2)

    def test_urgency_without_mapping_keeps_current_playback(self):
        instance, monitor = self.make_daemon(
            notification(
                "warp", hints=(("urgency", 2),), trailing_blank=False
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_debounce_burst_plays_once(self):
        # Ráfaga de la misma app: con la ventana default (2.0 s) solo
        # suena la primera; la segunda se descarta (DEB-001).
        payload = notification("chat") + notification(
            "chat", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_debounce_first_notification_always_plays(self):
        # Sin timestamp previo, la primera notificación de una app
        # siempre suena (DEB-001).
        instance, monitor = self.make_daemon(
            notification("chat", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_debounce_window_zero_disables(self):
        # Ventana 0 desactiva el debounce: todas las notificaciones de la
        # ráfaga suenan (DEB-001).
        self.write_config({"debounce_window": 0})
        payload = notification("chat") + notification(
            "chat", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        self.assertEqual(play.call_count, 2)

    def test_debounce_is_per_app(self):
        # Una app ruidosa no silencia a otras: cada app tiene su propia
        # ventana (DEB-001).
        payload = (
            notification("chat")
            + notification("chat", trailing_blank=False)
            + notification("mail", trailing_blank=False)
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        self.assertEqual(play.call_count, 2)

    def test_debounce_suppressed_notification_does_not_block_next(self):
        # Una notificación suprimida no actualiza el timestamp: la
        # siguiente audible de la misma app suena (DEB-001).
        payload = notification(
            "chat", hints=("suppress-sound",)
        ) + notification("chat", trailing_blank=False)
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_debounce_own_sound_without_config_does_not_block_next(self):
        # El descarte de sonido propio sin config (OWN-001) ocurre antes
        # del debounce y no actualiza el timestamp: la siguiente audible
        # de la misma app suena (DEB-001).
        payload = notification(
            "chat", hints=("sound-name",)
        ) + notification("chat", trailing_blank=False)
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_debounce_timestamp_not_updated_on_discard(self):
        # El timestamp se actualiza solo al reproducir; una notificación
        # descartada por debounce no lo mueve (DEB-001).
        cfg = {
            "enabled": True,
            "sound": "message",
            "debounce_window": 2.0,
            "apps": {},
        }
        instance = daemon.NotifyDaemon()
        with mock.patch.object(player, "play_choice") as play:
            instance._maybe_play("chat", set(), cfg)
            first_ts = instance._last_play_at["chat"]
            instance._maybe_play("chat", set(), cfg)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance._last_play_at["chat"], first_ts)

    def test_desktop_entry_hint_overrides_per_app_config_lookup(self):
        self.write_config(
            {
                "sound": "message",
                "apps": {"aimp": {"enabled": False, "sound": None}},
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "Any Song Title",
                hints=(("desktop-entry", "aimp"),),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()
        self.assertEqual(instance.seen, {"notify-sound", "aimp"})

    def test_sender_pid_resolution_canonicalizes_to_comm(self):
        with mock.patch.object(
            daemon, "_query_connection_pid", return_value=1234
        ), mock.patch.object(
            daemon, "_read_proc_comm", return_value="aimp"
        ), mock.patch.object(daemon, "_read_proc_cmdline_name") as cmdline:
            instance, monitor = self.make_daemon(
                notification("Pink Floyd - Time", trailing_blank=False),
                resolve_sender=True,
            )
            with mock.patch.object(player, "play_choice") as play:
                instance._reader(monitor)
        cmdline.assert_not_called()
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound", "aimp"})
        state = config.load_state()
        self.assertEqual(state["app_meta"]["aimp"]["comm"], "aimp")
        self.assertGreaterEqual(state["app_meta"]["aimp"]["seen_count"], 1)

    def test_sender_pid_resolution_skips_generic_comm_like_python(self):
        with mock.patch.object(
            daemon, "_query_connection_pid", return_value=1234
        ), mock.patch.object(
            daemon, "_read_proc_comm", return_value="python3"
        ), mock.patch.object(
            daemon, "_read_proc_cmdline_name", return_value="aimp"
        ):
            instance, monitor = self.make_daemon(
                notification("Some Song Title", trailing_blank=False),
                resolve_sender=True,
            )
            with mock.patch.object(player, "play_choice") as play:
                instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound", "aimp"})

    def test_sender_pid_resolution_falls_back_to_cmdline_when_comm_truncated(self):
        with mock.patch.object(
            daemon, "_query_connection_pid", return_value=1234
        ), mock.patch.object(
            daemon, "_read_proc_comm", return_value="telegram-deskto"
        ), mock.patch.object(
            daemon, "_read_proc_cmdline_name", return_value="telegram-desktop"
        ):
            instance, monitor = self.make_daemon(
                notification("New message", trailing_blank=False),
                resolve_sender=True,
            )
            with mock.patch.object(player, "play_choice"):
                instance._reader(monitor)
        self.assertEqual(instance.seen, {"notify-sound", "telegram-desktop"})

    def test_sender_pid_resolution_returns_none_when_dbus_send_missing(self):
        with mock.patch.object(
            daemon, "_query_connection_pid", return_value=None
        ):
            instance, monitor = self.make_daemon(
                notification("notify-send", trailing_blank=False),
                resolve_sender=True,
            )
            with mock.patch.object(player, "play_choice"):
                instance._reader(monitor)
        self.assertEqual(instance.seen, {"notify-sound", "notify-send"})

    def test_synonyms_lookup_canonicalizes_known_app_name(self):
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "aimp": {
                        "enabled": True,
                        "sound": None,
                        "synonyms": ["Canción conocida"],
                    }
                },
            }
        )
        with mock.patch.object(
            daemon, "_query_connection_pid", return_value=None
        ):
            instance, monitor = self.make_daemon(
                notification(
                    "Canción conocida", trailing_blank=False
                )
            )
            with mock.patch.object(player, "play_choice") as play:
                instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound", "aimp"})

    def test_app_meta_persists_comm_and_count_after_record(self):
        with mock.patch.object(
            daemon, "_query_connection_pid", return_value=4321
        ), mock.patch.object(
            daemon, "_read_proc_comm", return_value="vlc"
        ):
            instance, monitor = self.make_daemon(
                notification("Video title", trailing_blank=False),
                resolve_sender=True,
            )
            with mock.patch.object(player, "play_choice"):
                instance._reader(monitor)
        meta = config.load_state()["app_meta"]["vlc"]
        self.assertEqual(meta["comm"], "vlc")
        self.assertGreaterEqual(meta["seen_count"], 1)
        self.assertIn("last_seen", meta)

    def test_sound_name_hint_sets_has_own_sound(self):
        # OWN-001: notificacion con hint sound-name -> has_own_sound true
        # persistido en state.json y en el meta cache.
        instance, monitor = self.make_daemon(
            notification("warp", hints=("sound-name",), trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertIs(
            config.load_state()["app_meta"]["warp"]["has_own_sound"], True
        )
        self.assertIs(instance._meta_cache["warp"]["has_own_sound"], True)

    def test_sound_file_hint_sets_has_own_sound(self):
        # OWN-001: notificacion con hint sound-file -> has_own_sound true.
        instance, monitor = self.make_daemon(
            notification("warp", hints=("sound-file",), trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertIs(
            config.load_state()["app_meta"]["warp"]["has_own_sound"], True
        )

    def test_without_sound_hints_has_own_sound_is_false(self):
        # OWN-001: notificacion sin hints de sonido -> has_own_sound false.
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertIs(
            config.load_state()["app_meta"]["warp"]["has_own_sound"], False
        )

    def test_has_own_sound_is_not_sticky(self):
        # OWN-001: si la app manda sound-name (true) y luego deja de
        # mandarlo, el flag vuelve a false en la siguiente notificacion.
        payload = notification("warp", hints=("sound-name",)) + notification(
            "warp", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertIs(
            config.load_state()["app_meta"]["warp"]["has_own_sound"], False
        )
        self.assertIs(instance._meta_cache["warp"]["has_own_sound"], False)

    def test_sync_seen_with_state_relists_app_after_gui_reset(self):
        instance, monitor = self.make_daemon(
            notification("notify-send", trailing_blank=False)
        )
        instance._record_app("notify-send")
        self.assertIn("notify-send", config.load_state()["apps_seen"])

        config.save_state({"apps_seen": [], "app_meta": {}})
        self.assertEqual(config.load_state()["apps_seen"], [])

        instance._record_app("notify-send")
        self.assertIn(
            "notify-send", config.load_state()["apps_seen"]
        )

    def test_record_app_persists_seen_count_on_every_notification(self):
        instance, monitor = self.make_daemon(
            notification("notify-send", trailing_blank=False)
        )
        instance._record_app("notify-send")
        first_seen = config.load_state()["app_meta"]["notify-send"]["last_seen"]
        self.assertEqual(
            config.load_state()["app_meta"]["notify-send"]["seen_count"], 1
        )
        instance._record_app("notify-send")
        meta = config.load_state()["app_meta"]["notify-send"]
        self.assertEqual(meta["seen_count"], 2)
        self.assertGreaterEqual(meta["last_seen"], first_seen)

    def test_gtk_string_content_cannot_fake_array_end(self):
        instance, monitor = self.make_daemon(
            gtk_notification(
                body="before\n   ]\nmethod call fake",
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_suppress_sound_is_always_silent(self):
        instance, monitor = self.make_daemon(
            notification(
                "Vivaldi",
                hints=("suppress-sound",),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_registered_app_sound_overrides_global_sound(self):
        app_sound = "/tmp/notify-sound-test-I-Feel-Good.wav"
        self.write_config(
            {
                "sound": "message",
                "custom_sounds": [app_sound],
                "apps": {"warp": {"enabled": True, "sound": app_sound}},
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with(app_sound, volume=100)

    def test_app_volume_is_passed_to_player(self):
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {"enabled": True, "sound": None, "volume": 50}
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=50)

    def test_app_volume_applies_to_app_sound(self):
        app_sound = "/tmp/notify-sound-test-I-Feel-Good.wav"
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "sound": app_sound,
                        "volume": 50,
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with(app_sound, volume=50)

    def test_app_without_volume_plays_at_default_100(self):
        self.write_config(
            {
                "sound": "message",
                "apps": {"warp": {"enabled": True, "sound": None}},
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_zero_volume_app_is_silent_end_to_end(self):
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {"enabled": True, "sound": None, "volume": 0}
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(
            player, "play_sound", wraps=player.play_sound
        ) as play_sound, mock.patch.object(
            player.subprocess, "Popen"
        ) as popen:
            instance._reader(monitor)
        play_sound.assert_called_once_with("message", volume=0)
        popen.assert_not_called()

    def test_invalid_volume_in_config_is_normalized_to_100(self):
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {"enabled": True, "sound": None, "volume": "50"}
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_missing_canberra_does_not_stop_reader(self):
        payload = notification("first") + notification(
            "second", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(
            player.subprocess, "Popen", side_effect=FileNotFoundError
        ):
            instance._reader(monitor)
            self.assertFalse(player.play_sound("message"))
        self.assertEqual(instance.seen, {"notify-sound", "first", "second"})

    def test_audio_fallback_continues_after_nonzero_exit(self):
        first = mock.Mock()
        first.wait.return_value = 1
        second = mock.Mock()
        second.wait.return_value = 0
        commands = (["gst-launch-1.0"], ["ffplay"])
        with mock.patch.object(
            player.subprocess, "Popen", side_effect=[first, second]
        ):
            self.assertTrue(player._play_fallback(commands))
        first.wait.assert_called_once_with()
        second.wait.assert_called_once_with()

    def test_player_rejects_invalid_sound_ids_and_non_regular_files(self):
        self.assertFalse(player.play_sound("../message"))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "sound.wav"
            directory.mkdir()
            self.assertFalse(player.play_file(str(directory)))

    def test_monitor_exit_uses_exponential_backoff(self):
        instance = daemon.NotifyDaemon()
        instance.loop = FakeLoop(True)
        instance.accept_restarts = True
        with mock.patch.object(
            daemon.subprocess, "Popen", side_effect=OSError("gone")
        ), mock.patch.object(daemon.GLib, "timeout_add") as timeout_add:
            instance._start_monitor()
            self.assertEqual(timeout_add.call_args.args[0], 1000)
            instance.restart_pending = False
            instance._start_monitor()
            self.assertEqual(timeout_add.call_args.args[0], 2000)

    def test_killed_monitor_schedules_delayed_restart(self):
        instance = daemon.NotifyDaemon()
        instance.loop = FakeLoop(True)
        instance.accept_restarts = True
        monitor = FakeMonitor(b"")
        instance.monitor = monitor
        with mock.patch.object(daemon.GLib, "timeout_add") as timeout_add:
            instance._reader(monitor)
        timeout_add.assert_called_once()
        self.assertEqual(timeout_add.call_args.args[0], 1000)

    def test_own_notification_uses_app_sound_or_global(self):
        # La notificación propia (app_name `notify-send` + hint
        # `x-notify-sound-done`, enviada por el subcomando `notify-sound
        # done`) sigue el flujo normal: sonido per-app de "notify-sound"
        # o, si no tiene, el global (DONE-001).
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound"})

        app_sound = "/tmp/notify-sound-test-done.wav"
        self.write_config(
            {
                "sound": "message",
                "apps": {"notify-sound": {"enabled": True, "sound": app_sound}},
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with(app_sound, volume=100)

    def test_own_notification_uses_custom_app_sound(self):
        # Sonido per-app configurado para "notify-sound": la notificación
        # propia lo reproduce (DONE-001).
        app_sound = "/tmp/notify-sound-test-done.wav"
        self.write_config(
            {
                "sound": "message",
                "apps": {"notify-sound": {"enabled": True, "sound": app_sound}},
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with(app_sound, volume=100)

    def test_own_notification_without_sound_is_silent(self):
        # Caso degenerado: sin sonido per-app ni global (`sound` ausente o
        # None), la notificación propia no reproduce nada (DONE-001).
        for cfg in (
            {
                "enabled": True,
                "sound": None,
                "debounce_window": 2.0,
                "apps": {},
            },
            {
                "enabled": True,
                "sound": "",
                "debounce_window": 2.0,
                "apps": {},
            },
        ):
            with self.subTest(sound=cfg.get("sound")):
                instance, monitor = self.make_daemon(
                    notification(
                        "notify-send",
                        hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                        trailing_blank=False,
                    )
                )
                with mock.patch.object(
                    config, "load_config", return_value=cfg
                ), mock.patch.object(player, "play_choice") as play:
                    instance._reader(monitor)
                play.assert_not_called()

    def test_own_notification_skips_sender_resolution(self):
        # El reconocimiento de la notificación propia va por el hint
        # x-notify-sound-done, antes de la resolución de comm: no se
        # consulta el sender (DONE-001).
        resolve = mock.Mock(return_value="python3")
        with mock.patch.object(daemon, "_resolve_sender_to_comm", resolve):
            instance, monitor = self.make_daemon(
                notification(
                    "notify-send",
                    hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                    trailing_blank=False,
                ),
                resolve_sender=True,
            )
            with mock.patch.object(player, "play_choice") as play:
                instance._reader(monitor)
        resolve.assert_not_called()
        play.assert_called_once_with("message", volume=100)

    def test_own_notification_ignores_desktop_entry_hint(self):
        # El reconocimiento por hint x-notify-sound-done gana al hint
        # desktop-entry: la notificación propia nunca se canonaliza al
        # comm de otra app.
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(
                    ("desktop-entry", "aimp"),
                    ("x-notify-sound-done", "1"),
                    ("urgency", 1),
                ),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)
        self.assertEqual(instance.seen, {"notify-sound"})

    def test_other_app_uses_global_sound(self):
        # Una notificación con app_name `notify-send` pero SIN el hint
        # x-notify-sound-done no es propia: sigue el flujo canónico y usa
        # el sonido global (DONE-001).
        self.write_config({"sound": "message"})
        instance, monitor = self.make_daemon(
            notification("notify-send", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_own_notification_respects_suppress_sound(self):
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(
                    "suppress-sound",
                    ("x-notify-sound-done", "1"),
                    ("urgency", 1),
                ),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_own_notification_with_own_sound_without_config_is_not_played(self):
        # OWN-001: la app propia "notify-sound" no está en config.apps por
        # defecto; si una notificación propia trajera sound-name, no se
        # reproduce (mismo descarte que cualquier app sin configurar).
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(
                    "sound-name",
                    ("x-notify-sound-done", "1"),
                    ("urgency", 1),
                ),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_own_notification_respects_master_enabled(self):
        self.write_config({"enabled": False})
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_own_notification_respects_debounce(self):
        payload = notification(
            "notify-send",
            hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
        ) + notification(
            "notify-send",
            hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
            trailing_blank=False,
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_own_notification_respects_app_volume(self):
        self.write_config(
            {
                "apps": {
                    "notify-sound": {
                        "enabled": True,
                        "sound": None,
                        "volume": 50,
                    }
                }
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(("x-notify-sound-done", "1"), ("urgency", 1)),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=50)

    def test_own_notification_respects_urgency_rule(self):
        # DONE-001 + RULE-001: la notificación propia con urgency=2 y una
        # regla de urgencia configurada reproduce el sonido de la regla
        # (URG-001 migrado: urgency se configura como regla).
        critical_sound = "/tmp/notify-sound-test-critical.wav"
        self.write_config(
            {
                "apps": {
                    "notify-sound": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "urgency",
                                    "op": "eq",
                                    "value": 2,
                                },
                                "action": "sound",
                                "sound": critical_sound,
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "notify-send",
                hints=(("x-notify-sound-done", "1"), ("urgency", 2)),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with(critical_sound, volume=100)

    def test_daemon_pre_registers_own_app(self):
        # Al arrancar, el daemon pre-registra "notify-sound" en apps_seen
        # para que la GUI la muestre sin necesidad de que suene la primera
        # notificación; no incrementa seen_count ni last_seen: es un
        # registro inicial, no una notificación recibida (DONE-001).
        instance = daemon.NotifyDaemon()
        self.assertIn("notify-sound", instance.seen)
        state = config.load_state()
        self.assertIn("notify-sound", state["apps_seen"])
        meta = state["app_meta"].get("notify-sound", {})
        self.assertEqual(meta.get("seen_count", 0), 0)
        self.assertNotIn("last_seen", meta)


    def test_rule_sound_matches_plays_rule_sound(self):
        # RULE-001: regla sobre body con contains que matchea -> se
        # reproduce el sonido de la regla (no el del app/global).
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="esperando permiso", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)

    def test_rule_silence_matches_no_play(self):
        # RULE-001: regla con action silence que matchea -> no se
        # reproduce nada.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "spam",
                                },
                                "action": "silence",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="spam publicidad", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_rule_first_match_wins(self):
        # RULE-001: si varias reglas matchean, gana la primera definida
        # (orden de rules).
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": "first",
                            },
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": "second",
                            },
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="esperando permiso", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("first", volume=100)

    def test_rule_no_match_fallback_current(self):
        # RULE-001: sin match -> comportamiento actual (sonido del
        # app/global).
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "nunca aparece",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="Body", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_rule_summary_contains(self):
        # RULE-001: regla sobre summary con contains -> matchea.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "summary",
                                    "op": "contains",
                                    "value": "Summ",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)

    def test_rule_urgency_eq(self):
        # RULE-001: urgency se configura como regla sobre hint (eq con
        # int) -> matchea (generaliza URG-001).
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "urgency",
                                    "op": "eq",
                                    "value": 2,
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "warp", hints=(("urgency", 2),), trailing_blank=False
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)

    def test_rule_regex_matches(self):
        # RULE-001: regla con op regex y patron valido -> matchea.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "regex",
                                    "value": r"^Body$",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="Body", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)

    def test_rule_starts_with_matches(self):
        # RULE-001: regla con op starts_with sobre body que empieza por el
        # valor -> matchea y reproduce el sonido de la regla.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "starts_with",
                                    "value": "Latest",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="Latest news", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)

    def test_rule_starts_with_no_match(self):
        # RULE-001: starts_with sin match -> fallback al sonido del
        # app/global.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "starts_with",
                                    "value": "Latest",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="Wants to run", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_rule_ends_with_matches(self):
        # RULE-001: regla con op ends_with sobre summary que termina por
        # el valor -> matchea y reproduce el sonido de la regla.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "summary",
                                    "op": "ends_with",
                                    "value": "finished",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "warp", summary="Build finished", trailing_blank=False
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)

    def test_rule_ends_with_no_match(self):
        # RULE-001: ends_with sin match -> fallback al sonido del
        # app/global.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "summary",
                                    "op": "ends_with",
                                    "value": "finished",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "warp", summary="Build started", trailing_blank=False
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_rule_starts_with_on_int_field_no_match(self):
        # RULE-001: starts_with solo aplica a strings; sobre urgency (int)
        # no matchea -> fallback al sonido del app/global.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "urgency",
                                    "op": "starts_with",
                                    "value": "2",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "warp", hints=(("urgency", 2),), trailing_blank=False
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_rule_field_unavailable_no_match(self):
        # RULE-001: hint arbitrario no extraido por el parser (p. ej.
        # category) no matchea -> fallback al sonido del app/global.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "category",
                                    "op": "contains",
                                    "value": "x",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message", volume=100)

    def test_rule_does_not_override_suppress_sound(self):
        # RULE-001: suppress-sound tiene prioridad sobre las reglas.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "warp",
                hints=("suppress-sound",),
                body="esperando permiso",
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_rule_does_not_override_app_disabled(self):
        # RULE-001: app deshabilitada -> nada; las reglas no la reactivan.
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": False,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification("warp", body="esperando permiso", trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_rule_does_not_override_own_sound_no_config(self):
        # OWN-001: el descarte de sonido propio sin config ocurre antes
        # de evaluar reglas (que solo existen para apps configuradas):
        # app sin entrada en config.apps + sound-name -> no reproduce.
        self.write_config({"sound": "message"})
        instance, monitor = self.make_daemon(
            notification("warp", hints=("sound-name",), trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_not_called()

    def test_rule_sound_plays_when_rule_matches(self):
        # RULE-001: si una regla matchea, se reproduce su sonido aunque la
        # notificación traiga urgency (URG-001 migrado: urgency es una
        # regla más; no hay override legacy).
        rule_sound = "/tmp/notify-sound-test-rule.wav"
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": rule_sound,
                            }
                        ],
                    }
                },
            }
        )
        instance, monitor = self.make_daemon(
            notification(
                "warp",
                hints=(("urgency", 2),),
                body="esperando permiso",
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with(rule_sound, volume=100)

    def test_rule_sound_updates_last_play_at(self):
        # RULE-001: reproducir por regla actualiza _last_play_at; la
        # siguiente notificacion dentro de la ventana de debounce se
        # descarta (DEB-001).
        self.write_config(
            {
                "sound": "message",
                "apps": {
                    "warp": {
                        "enabled": True,
                        "rules": [
                            {
                                "match": {
                                    "field": "body",
                                    "op": "contains",
                                    "value": "esperando",
                                },
                                "action": "sound",
                                "sound": "custom",
                            }
                        ],
                    }
                },
            }
        )
        payload = notification(
            "warp", body="esperando permiso"
        ) + notification(
            "warp", body="esperando permiso", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("custom", volume=100)


class ProcessRegressionTests(unittest.TestCase):
    def write_executable(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        content = textwrap.dedent(content).lstrip()
        if content.startswith("#!/usr/bin/python3"):
            content = f"#!{sys.executable}" + content[len("#!/usr/bin/python3") :]
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def write_monitor(self, directory):
        self.write_executable(
            directory / "dbus-monitor",
            '''
            #!/usr/bin/python3
            import os
            import pathlib
            import sys
            import time

            log = os.environ.get("MONITOR_LOG")
            if log:
                with open(log, "a", encoding="utf-8") as stream:
                    stream.write(f"{time.monotonic()}\\n")
            payload = os.environ.get("MONITOR_PAYLOAD")
            if payload:
                sys.stdout.buffer.write(pathlib.Path(payload).read_bytes())
                sys.stdout.flush()
            if os.environ.get("MONITOR_EXIT") != "1":
                time.sleep(60)
            ''',
        )

    def write_canberra(self, directory):
        self.write_executable(
            directory / "canberra-gtk-play",
            '''
            #!/usr/bin/python3
            import os
            import sys

            with open(os.environ["CANBERRA_LOG"], "a", encoding="utf-8") as stream:
                stream.write(" ".join(sys.argv[1:]) + "\\n")
            ''',
        )

    def write_dbus_send(self, directory):
        # Stub that prints nothing: the sender resolver in the daemon
        # gets no uint32 line and falls back to None, isolating process
        # tests from the host's real D-Bus session.
        self.write_executable(
            directory / "dbus-send",
            '''
            #!/usr/bin/python3
            ''',
        )

    def write_config(self, config_dir):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "sound": "message",
                    "custom_sounds": [],
                    "autostart": True,
                    "apps": {},
                }
            ),
            encoding="utf-8",
        )
        (config_dir / "state.json").write_text(
            '{"apps_seen": []}', encoding="utf-8"
        )

    def start_daemon(self, env):
        child_env = os.environ.copy()
        child_env.update(env)
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env.get("PATH_ONLY") == "1":
            child_env["PATH"] = env["FAKE_BIN"]
        else:
            child_env["PATH"] = (
                f'{env["FAKE_BIN"]}{os.pathsep}{child_env.get("PATH", "")}'
            )
        return subprocess.Popen(
            [sys.executable, str(ROOT / "notify-sound"), "--daemon"],
            cwd=ROOT,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def stop_daemon(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.communicate(timeout=1)

    def environment(self, root, fake_bin, payload=None):
        config_dir = root / "config" / "notify-sound"
        runtime_dir = root / "runtime"
        fake_bin.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir()
        self.write_config(config_dir)
        self.write_dbus_send(fake_bin)
        values = {
            "FAKE_BIN": str(fake_bin),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "CANBERRA_LOG": str(root / "canberra.log"),
        }
        if payload is not None:
            payload_path = root / "payload"
            payload_path.write_bytes(payload)
            values["MONITOR_PAYLOAD"] = str(payload_path)
        return values

    def test_single_notification_reaches_canberra_watcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            self.write_canberra(fake_bin)
            env = self.environment(
                root,
                fake_bin,
                notification("notify-send"),
            )
            process = self.start_daemon(env)
            try:
                log = Path(env["CANBERRA_LOG"])
                self.assertTrue(self.wait_for(log.exists))
                self.assertIn("-i message", log.read_text(encoding="utf-8"))
                self.assertIsNone(process.poll())
            finally:
                self.stop_daemon(process)

    def test_gtk_notification_reaches_canberra_watcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            self.write_canberra(fake_bin)
            env = self.environment(
                root,
                fake_bin,
                gtk_notification("org.gnome.Ptyxis", trailing_blank=False),
            )
            process = self.start_daemon(env)
            try:
                log = Path(env["CANBERRA_LOG"])
                self.assertTrue(self.wait_for(log.exists))
                self.assertIn("-i message", log.read_text(encoding="utf-8"))
                self.assertIsNone(process.poll())
            finally:
                self.stop_daemon(process)

    def test_notification_with_hint_does_not_reach_canberra(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            self.write_canberra(fake_bin)
            env = self.environment(
                root,
                fake_bin,
                notification(
                    "telegram", hints=("sound-name",)
                ),
            )
            process = self.start_daemon(env)
            try:
                state = Path(env["XDG_CONFIG_HOME"]) / "notify-sound/state.json"
                self.assertTrue(
                    self.wait_for(
                        lambda: state.exists()
                        and "telegram" in state.read_text(encoding="utf-8")
                    )
                )
                time.sleep(0.2)
                self.assertFalse(Path(env["CANBERRA_LOG"]).exists())
                self.assertIsNone(process.poll())
            finally:
                self.stop_daemon(process)

    def test_missing_canberra_keeps_daemon_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            env = self.environment(
                root,
                fake_bin,
                notification("first")
                + notification("second"),
            )
            env["PATH_ONLY"] = "1"
            process = self.start_daemon(env)
            try:
                state = Path(env["XDG_CONFIG_HOME"]) / "notify-sound/state.json"
                self.assertTrue(
                    self.wait_for(
                        lambda: state.exists()
                        and all(
                            name in state.read_text(encoding="utf-8")
                            for name in ("first", "second")
                        )
                    )
                )
                self.assertIsNone(process.poll())
            finally:
                self.stop_daemon(process)

    def test_monitor_restart_uses_backoff_without_busy_loop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            env = self.environment(root, fake_bin)
            env["MONITOR_LOG"] = str(root / "monitor.log")
            env["MONITOR_EXIT"] = "1"
            process = self.start_daemon(env)
            try:
                log = Path(env["MONITOR_LOG"])
                self.assertTrue(
                    self.wait_for(
                        lambda: log.exists()
                        and len(log.read_text(encoding="utf-8").splitlines())
                        >= 2,
                        timeout=4,
                    )
                )
                timestamps = [
                    float(value)
                    for value in log.read_text(encoding="utf-8").splitlines()
                ]
                self.assertLessEqual(len(timestamps), 4)
                self.assertGreaterEqual(timestamps[1] - timestamps[0], 0.8)
                self.assertIsNone(process.poll())
            finally:
                self.stop_daemon(process)

    def test_second_daemon_start_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            env = self.environment(root, fake_bin)
            process = self.start_daemon(env)
            try:
                pid_file = Path(env["XDG_RUNTIME_DIR"]) / "notify-sound.pid"
                self.assertTrue(self.wait_for(pid_file.exists))
                second = self.start_daemon(env)
                output, _ = second.communicate(timeout=5)
                self.assertEqual(second.returncode, 1)
                self.assertIn("ya está corriendo", output)
            finally:
                self.stop_daemon(process)

    def test_daemon_stops_cleanly_after_sigterm(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            self.write_monitor(fake_bin)
            env = self.environment(root, fake_bin)
            env["MONITOR_LOG"] = str(root / "monitor.log")
            process = self.start_daemon(env)
            try:
                self.assertTrue(
                    self.wait_for(Path(env["MONITOR_LOG"]).exists)
                )
                process.terminate()
                process.wait(timeout=3)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    self.stop_daemon(process)
                else:
                    process.communicate(timeout=1)

    def test_custom_prefix_generates_matching_autostart_and_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            prefix = root / "opt" / "test"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PREFIX": str(prefix),
                    "XDG_CONFIG_HOME": str(home / ".config"),
                }
            )
            subprocess.run(
                [str(ROOT / "install.sh")],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            autostart = home / ".config/autostart/notify-sound.desktop"
            service = prefix / "share/notify-sound/notify-sound.service"
            expected = f'"{prefix}/bin/notify-sound" --daemon'
            self.assertIn(f"Exec={expected}", autostart.read_text(encoding="utf-8"))
            self.assertIn(
                f"ExecStart={expected}", service.read_text(encoding="utf-8")
            )
            subprocess.run(
                [str(ROOT / "install.sh"), "--no-autostart"],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertFalse(autostart.exists())

    def test_installer_rejects_unsafe_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env.update(
                {
                    "HOME": temp,
                    "PREFIX": f"{temp}/bad\nExecStart=/tmp/attacker",
                }
            )
            result = subprocess.run(
                [str(ROOT / "install.sh")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)


class GuiTests(unittest.TestCase):
    def test_activation_reuses_one_window_and_holds_once(self):
        from notify_sound import gui

        app = gui.NotifyApplication()
        window = mock.Mock()
        with mock.patch.object(gui, "NotifyWindow", return_value=window) as new_window:
            with mock.patch.object(app, "hold") as hold:
                app.do_activate()
                app.do_activate()

        new_window.assert_called_once_with(app)
        self.assertEqual(window.present.call_count, 2)
        hold.assert_called_once_with()

    def test_close_clears_window_reference(self):
        from notify_sound import gui

        app = gui.NotifyApplication()
        window = mock.Mock()
        app.window = window
        app._held = True
        with mock.patch.object(app, "release") as release:
            self.assertFalse(app._on_window_close(window))
        release.assert_called_once_with()
        self.assertFalse(app._held)
        self.assertIsNone(app.window)

    def test_display_name_prefers_alias_over_canonical(self):
        from notify_sound import gui

        window = _bare_window({"apps": {"aimp": {"name": "AIMP"}}})
        self.assertEqual(window._display_name("aimp"), "AIMP")
        self.assertEqual(window._display_name("telegram"), "telegram")

    def test_app_rename_save_updates_config_and_label(self):
        from notify_sound import gui

        cfg = {"apps": {"aimp": {"enabled": True, "sound": None}}}
        window = _bare_window(cfg)
        label = mock.Mock()
        popover = mock.Mock()
        entry = mock.Mock()
        entry.get_text.return_value = "  AIMP  "
        window.app_rows = {"aimp": {"name_label": label}}
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_app_rename_save(window, None, "aimp", entry, popover)
        save.assert_called_once_with()
        self.assertEqual(cfg["apps"]["aimp"]["name"], "AIMP")
        label.set_text.assert_called_once_with("AIMP")
        popover.popdown.assert_called_once_with()

    def test_app_rename_save_rejects_empty_and_too_long_aliases(self):
        from notify_sound import gui

        cfg = {"apps": {"aimp": {"enabled": True, "sound": None}}}
        window = _bare_window(cfg)

        label = mock.Mock()
        popover = mock.Mock()
        entry = mock.Mock()
        entry.get_text.return_value = "   "
        window.app_rows = {"aimp": {"name_label": label}}
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_app_rename_save(window, None, "aimp", entry, popover)
        save.assert_not_called()
        label.set_text.assert_not_called()
        popover.popdown.assert_not_called()

        long_value = "x" * (config.MAX_APP_NAME_LENGTH + 1)
        entry.get_text.return_value = long_value
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_app_rename_save(window, None, "aimp", entry, popover)
        save.assert_not_called()
        self.assertNotIn("name", cfg["apps"]["aimp"])

    def test_app_rename_reset_removes_alias_and_restores_label(self):
        from notify_sound import gui

        cfg = {"apps": {"aimp": {"enabled": True, "sound": None, "name": "Me AIM"}}}
        window = _bare_window(cfg)
        label = mock.Mock()
        popover = mock.Mock()
        window.app_rows = {"aimp": {"name_label": label}}
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_app_rename_reset(window, None, "aimp", popover)
        save.assert_called_once_with()
        self.assertNotIn("name", cfg["apps"]["aimp"])
        label.set_text.assert_called_once_with("aimp")
        popover.popdown.assert_called_once_with()

    def test_app_rename_reset_is_noop_when_no_alias(self):
        from notify_sound import gui

        cfg = {"apps": {"aimp": {"enabled": True, "sound": None}}}
        window = _bare_window(cfg)
        label = mock.Mock()
        popover = mock.Mock()
        window.app_rows = {"aimp": {"name_label": label}}
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_app_rename_reset(window, None, "aimp", popover)
        save.assert_not_called()
        label.set_text.assert_not_called()
        popover.popdown.assert_called_once_with()

    def test_rename_to_existing_alias_prompts_merge_without_saving(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "songA": {"enabled": True, "sound": None, "name": "AIMP"},
                "songB": {"enabled": True, "sound": None},
            }
        }
        window = _bare_window(cfg)
        label = mock.Mock()
        popover = mock.Mock()
        entry = mock.Mock()
        entry.get_text.return_value = "AIMP"
        window.app_rows = {"songB": {"name_label": label}}
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(window, "_prompt_merge_alias") as prompt:
            gui.NotifyWindow._on_app_rename_save(
                window, None, "songB", entry, popover
            )
        save.assert_not_called()
        label.set_text.assert_not_called()
        popover.popdown.assert_called_once_with()
        prompt.assert_called_once_with("songB", "AIMP", "songA")

    def test_merge_app_into_moves_synonyms_and_removes_source(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "songB": {"enabled": False, "sound": "/tmp/x.wav"},
                "aimp": {"enabled": True, "sound": None},
            }
        }
        window = _bare_window(cfg)
        source_row = mock.Mock()
        window.app_rows = {
            "songB": {"row": source_row},
            "aimp": {"row": mock.Mock()},
        }
        fake_state = {"apps_seen": ["songB", "aimp"], "app_meta": {}}
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(config, "load_state", return_value=fake_state), \
             mock.patch.object(config, "save_state") as save_state:
            window._merge_app_into("songB", "aimp")
        save.assert_called_once_with()
        save_state.assert_called_once()
        self.assertNotIn("songB", cfg["apps"])
        self.assertIn("songB", cfg["apps"]["aimp"]["synonyms"])
        self.assertFalse(cfg["apps"]["aimp"]["enabled"])
        self.assertEqual(cfg["apps"]["aimp"]["sound"], "/tmp/x.wav")
        window.apps_list.remove.assert_called_once_with(source_row)
        self.assertNotIn("songB", window.app_rows)

    def test_merge_dialog_choice_confirms_fusion(self):
        from notify_sound import gui

        popover = mock.Mock()
        window = _bare_window({"apps": {}})
        with mock.patch.object(window, "_merge_app_into") as merge:
            gui.NotifyWindow._on_merge_confirm(
                window, None, "songB", "aimp", popover
            )
        popover.popdown.assert_called_once_with()
        merge.assert_called_once_with("songB", "aimp")

    def test_merge_dialog_choice_cancel_does_nothing(self):
        from notify_sound import gui

        popover = mock.Mock()
        window = _bare_window({"apps": {}})
        with mock.patch.object(window, "_merge_app_into") as merge:
            gui.NotifyWindow._on_merge_cancel(window, None, popover)
        popover.popdown.assert_called_once_with()
        merge.assert_not_called()

    def test_format_app_info_lists_canonical_alias_comm_and_synonyms(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "aimp": {
                    "name": "AIMP",
                    "synonyms": ["Song A", "Song B"],
                }
            }
        }
        window = _bare_window(cfg)
        fake_state = {
            "apps_seen": ["aimp"],
            "app_meta": {
                "aimp": {"seen_count": 5, "comm": "aimp", "last_seen": 1700000000.0},
            },
        }
        with mock.patch.object(config, "load_state", return_value=fake_state):
            info = window._format_app_info("aimp")
        self.assertIn("Nombre de notificación: aimp", info)
        self.assertIn("Mostrado como: AIMP", info)
        self.assertIn("aimp", info)
        self.assertIn("Número de sinónimos: 2", info)
        self.assertIn("Notificaciones: 5", info)
        self.assertIn("Última vista:", info)
        self.assertNotIn("aún no se ha observado", info)

    def test_format_app_info_notes_legacy_when_no_app_meta(self):
        from notify_sound import gui

        cfg = {"apps": {"warp": {"enabled": True, "sound": None}}}
        window = _bare_window(cfg)
        fake_state = {"apps_seen": ["warp"], "app_meta": {}}
        with mock.patch.object(config, "load_state", return_value=fake_state):
            info = window._format_app_info("warp")
        self.assertIn("aún no se ha observado", info)
        self.assertIn("Proceso emisor: —", info)

    def test_app_remove_clears_config_state_and_synonym_refs(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "warp": {"enabled": True, "sound": None},
                "aimp": {
                    "enabled": True, "sound": None,
                    "synonyms": ["songA", "warp"],
                },
            }
        }
        window = _bare_window(cfg)
        warp_row = mock.Mock()
        window.app_rows = {"warp": {"row": warp_row}}
        fake_state = {
            "apps_seen": ["warp", "aimp"],
            "app_meta": {"warp": {"seen_count": 2}},
        }
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(config, "load_state", return_value=fake_state), \
             mock.patch.object(config, "save_state") as save_state:
            window._on_app_remove(None, "warp")
        save.assert_called_once_with()
        save_state.assert_called_once()
        self.assertNotIn("warp", cfg["apps"])
        self.assertNotIn("warp", cfg["apps"]["aimp"]["synonyms"])
        window.apps_list.remove.assert_called_once_with(warp_row)
        self.assertNotIn("warp", window.app_rows)
        saved = save_state.call_args.args[0]
        self.assertNotIn("warp", saved["apps_seen"])
        self.assertNotIn("warp", saved["app_meta"])

    def test_reset_apps_confirm_empties_config_state_and_rows(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "warp": {"enabled": True, "sound": None, "name": "Warp"},
                "aimp": {"enabled": False, "sound": None, "synonyms": ["x"]},
            }
        }
        window = _bare_window(cfg)
        window.app_rows = {
            "warp": {"row": mock.Mock()},
            "aimp": {"row": mock.Mock()},
        }
        popover = mock.Mock()
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(config, "save_state") as save_state:
            window._on_reset_apps_confirm(None, popover)
        popover.popdown.assert_called_once_with()
        save.assert_called_once_with()
        save_state.assert_called_once()
        self.assertEqual(cfg["apps"], {})
        self.assertEqual(window.app_rows, {})
        self.assertEqual(save_state.call_args.args[0]["apps_seen"], [])
        self.assertEqual(save_state.call_args.args[0]["app_meta"], {})
        self.assertEqual(window.apps_list.remove.call_count, 2)

    def test_reset_apps_cancel_does_nothing(self):
        from notify_sound import gui

        window = _bare_window({"apps": {"warp": {"enabled": True}}})
        popover = mock.Mock()
        with mock.patch.object(window, "_on_reset_apps_confirm") as confirm, \
             mock.patch.object(window, "_save") as save:
            window._on_reset_apps_cancel(None, popover)
        popover.popdown.assert_called_once_with()
        save.assert_not_called()
        confirm.assert_not_called()

    def test_synonym_restore_splits_back_into_own_entry(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "aimp": {
                    "enabled": True, "sound": None,
                    "synonyms": ["songA", "songB"],
                }
            }
        }
        window = _bare_window(cfg)
        window.app_rows = {
            "aimp": {
                "row": mock.Mock(),
                "info_popover": mock.Mock(),
                "info_synonyms_box": mock.Mock(),
                "info_label": mock.Mock(),
            }
        }
        fake_state = {
            "apps_seen": ["aimp"],
            "app_meta": {"aimp": {"seen_count": 3}},
        }
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(config, "load_state", return_value=fake_state), \
             mock.patch.object(config, "save_state") as save_state, \
             mock.patch.object(window, "_ensure_app_row") as ensure, \
             mock.patch.object(window, "_refresh_app_sensitivity"), \
             mock.patch.object(window, "_refresh_info_synonyms"):
            window._on_synonym_restore(None, "aimp", "songA")
        save.assert_called_once_with()
        save_state.assert_called_once()
        ensure.assert_called_once_with("songA")
        self.assertEqual(cfg["apps"]["aimp"]["synonyms"], ["songB"])
        saved = save_state.call_args.args[0]
        self.assertIn("songA", saved["apps_seen"])

    def test_synonym_restore_removes_synonyms_key_when_last_one(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "aimp": {
                    "enabled": True, "sound": None, "synonyms": ["songA"],
                }
            }
        }
        window = _bare_window(cfg)
        window.app_rows = {
            "aimp": {
                "row": mock.Mock(),
                "info_popover": mock.Mock(),
                "info_synonyms_box": mock.Mock(),
                "info_label": mock.Mock(),
            }
        }
        fake_state = {"apps_seen": ["aimp"], "app_meta": {}}
        with mock.patch.object(window, "_save"), \
             mock.patch.object(config, "load_state", return_value=fake_state), \
             mock.patch.object(config, "save_state"), \
             mock.patch.object(window, "_ensure_app_row"), \
             mock.patch.object(window, "_refresh_app_sensitivity"), \
             mock.patch.object(window, "_refresh_info_synonyms"):
            window._on_synonym_restore(None, "aimp", "songA")
        self.assertNotIn("synonyms", cfg["apps"]["aimp"])

    def test_populate_app_dropdown_fills_choices_for_new_row(self):
        from notify_sound import gui

        cfg = {
            "sound": "message",
            "custom_sounds": [],
            "apps": {"newapp": {"enabled": True, "sound": None}},
        }
        window = _bare_window(cfg)
        window.theme_ids = ["message", "bell"]
        dropdown = mock.Mock()
        entry = {"dropdown": dropdown}
        window._populate_app_dropdown("newapp", entry)
        dropdown.set_model.assert_called_once()
        model_arg = dropdown.set_model.call_args.args[0]
        self.assertEqual(model_arg.get_string(0), gui.INHERITED)
        self.assertEqual(model_arg.get_string(1), "message")
        self.assertEqual(model_arg.get_string(2), "bell")
        dropdown.set_selected.assert_called_once_with(0)

    def test_app_row_label_uses_width_chars_and_icon_play(self):
        from notify_sound import gui

        if not os.environ.get("DISPLAY") and not os.environ.get(
            "WAYLAND_DISPLAY"
        ):
            self.skipTest("requires a display to instantiate GTK widgets")

        cfg = {"apps": {"warp": {"enabled": True, "sound": None}}}
        window = _bare_window(cfg)
        window.theme_ids = ["message"]
        window.apps_list = mock.Mock()

        original_label = gui.Gtk.Label

        class SpyLabel(original_label):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._spy_width = None
                self._spy_max_width = None

            def set_width_chars(self, n):
                self._spy_width = n
                super().set_width_chars(n)

            def set_max_width_chars(self, n):
                self._spy_max_width = n
                super().set_max_width_chars(n)

        class SpyButton(gui.Gtk.Button):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._spy_icon = None

            def set_icon_name(self, name):
                self._spy_icon = name
                super().set_icon_name(name)

        with mock.patch.object(gui.Gtk, "Label", SpyLabel), \
             mock.patch.object(gui.Gtk, "Button", SpyButton):
            window._ensure_app_row("warp")

        entry = window.app_rows["warp"]
        label = entry["name_label"]
        self.assertEqual(label._spy_width, 28)
        self.assertEqual(label._spy_max_width, 50)

    def test_sort_apps_by_name_and_by_count(self):
        from notify_sound import gui

        cfg = {
            "apps": {
                "warp": {"enabled": True, "sound": None},
                "aimp": {"enabled": True, "sound": None, "name": "AIMP"},
                "vlc": {"enabled": True, "sound": None},
            }
        }
        window = _bare_window(cfg)
        fake_state = {
            "apps_seen": ["warp", "aimp", "vlc"],
            "app_meta": {
                "warp": {"seen_count": 5},
                "aimp": {"seen_count": 12},
                "vlc": {"seen_count": 1},
            },
        }
        with mock.patch.object(config, "load_state", return_value=fake_state):
            self.assertEqual(
                window._sorted_app_names(0), ["warp", "aimp", "vlc"]
            )
            self.assertEqual(
                window._sorted_app_names(1), ["aimp", "vlc", "warp"]
            )
            self.assertEqual(
                window._sorted_app_names(2), ["aimp", "warp", "vlc"]
            )

    def test_custom_sounds_add_appends_file(self):
        from notify_sound import gui

        cfg = {"custom_sounds": ["/old.wav"]}
        window = _bare_window(cfg)
        gfile = mock.Mock()
        gfile.get_path.return_value = "/new.wav"
        dialog = mock.Mock()
        dialog.open_finish.return_value = gfile
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(window, "_refresh_custom_list") as refresh, \
             mock.patch.object(window, "_rebuild_all_dropdowns") as rebuild:
            gui.NotifyWindow._on_add_custom_done(window, dialog, mock.Mock())
        save.assert_called_once_with()
        refresh.assert_called_once_with()
        rebuild.assert_called_once_with()
        self.assertIn("/new.wav", cfg["custom_sounds"])
        self.assertIn("/old.wav", cfg["custom_sounds"])


class GuiVolumeTests(unittest.TestCase):
    """Tests del slider de volumen por app (VOL-001)."""

    def _row_window(self, cfg, app_name="warp"):
        if not os.environ.get("DISPLAY") and not os.environ.get(
            "WAYLAND_DISPLAY"
        ):
            self.skipTest("requires a display to instantiate GTK widgets")
        window = _bare_window(cfg)
        window.theme_ids = ["message"]
        window._ensure_app_row(app_name)
        return window

    def test_app_row_contains_volume_scale_with_0_100_adjustment(self):
        from notify_sound import gui

        cfg = {"apps": {"warp": {"enabled": True, "sound": None}}}
        window = self._row_window(cfg)
        entry = window.app_rows["warp"]
        self.assertIn("volume_scale", entry)
        scale = entry["volume_scale"]
        self.assertIsInstance(scale, gui.Gtk.Scale)
        adjustment = scale.get_adjustment()
        self.assertEqual(adjustment.get_lower(), 0)
        self.assertEqual(adjustment.get_upper(), 100)

    def test_volume_scale_reflects_saved_value(self):
        cfg = {
            "apps": {"warp": {"enabled": True, "sound": None, "volume": 30}}
        }
        window = self._row_window(cfg)
        scale = window.app_rows["warp"]["volume_scale"]
        self.assertEqual(scale.get_value(), 30)

    def test_volume_change_persists_rounded_value(self):
        cfg = {
            "apps": {"warp": {"enabled": True, "sound": None, "volume": 100}}
        }
        window = self._row_window(cfg)
        scale = window.app_rows["warp"]["volume_scale"]
        with mock.patch.object(config, "save_config") as save_config:
            scale.get_adjustment().set_value(50)
        save_config.assert_called_once()
        saved = save_config.call_args.args[0]
        self.assertEqual(saved["apps"]["warp"]["volume"], 50)
        self.assertEqual(cfg["apps"]["warp"]["volume"], 50)

    def test_test_app_plays_with_app_volume(self):
        cfg = {
            "sound": "message",
            "apps": {"warp": {"enabled": True, "sound": None, "volume": 30}},
        }
        window = _bare_window(cfg)
        with mock.patch.object(player, "play_choice") as play:
            window._on_test_app(None, "warp")
        play.assert_called_once_with("message", volume=30)

    def test_test_app_plays_at_default_volume_when_missing(self):
        cfg = {
            "sound": "message",
            "apps": {"warp": {"enabled": True, "sound": None}},
        }
        window = _bare_window(cfg)
        with mock.patch.object(player, "play_choice") as play:
            window._on_test_app(None, "warp")
        play.assert_called_once_with("message", volume=100)

    def test_refresh_app_sensitivity_disables_volume_scale_when_master_off(self):
        cfg = {
            "enabled": False,
            "apps": {"warp": {"enabled": True, "sound": None}},
        }
        window = self._row_window(cfg)
        entry = window.app_rows["warp"]
        window._refresh_app_sensitivity()
        self.assertFalse(entry["volume_scale"].get_sensitive())
        self.assertFalse(entry["dropdown"].get_sensitive())
        self.assertFalse(entry["switch"].get_sensitive())

    def test_refresh_app_sensitivity_enables_volume_scale_when_master_on(self):
        cfg = {
            "enabled": True,
            "apps": {"warp": {"enabled": True, "sound": None}},
        }
        window = self._row_window(cfg)
        entry = window.app_rows["warp"]
        window._refresh_app_sensitivity()
        self.assertTrue(entry["volume_scale"].get_sensitive())


class GuiRulesTests(unittest.TestCase):
    """Tests del diálogo de reglas por app (RULE-001)."""

    def _rules_window(self, cfg):
        from notify_sound import gui

        window = _bare_window(cfg)
        window.theme_ids = ["message", "bell"]
        return window

    def _row_window(self, cfg, app_name="warp"):
        if not os.environ.get("DISPLAY") and not os.environ.get(
            "WAYLAND_DISPLAY"
        ):
            self.skipTest("requires a display to instantiate GTK widgets")
        window = _bare_window(cfg)
        window.theme_ids = ["message"]
        window._ensure_app_row(app_name)
        return window

    def test_get_app_rules_empty(self):
        from notify_sound import gui

        window = self._rules_window({"apps": {"warp": {"enabled": True}}})
        self.assertEqual(window._get_app_rules("warp"), [])

    def test_get_app_rules_existing(self):
        from notify_sound import gui

        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": "message",
            }
        ]
        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": rules}}}
        )
        self.assertEqual(window._get_app_rules("warp"), rules)

    def test_add_app_rule_adds_default(self):
        from notify_sound import gui

        window = self._rules_window({"apps": {"warp": {"enabled": True}}})
        with mock.patch.object(window, "_save") as save:
            window._add_app_rule("warp")
        save.assert_called_once_with()
        rules = window._get_app_rules("warp")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["match"]["field"], "body")
        self.assertEqual(rules[0]["match"]["op"], "contains")
        self.assertEqual(rules[0]["action"], "sound")
        self.assertEqual(rules[0]["sound"], "message")

    def test_add_app_rule_respects_max_rules(self):
        from notify_sound import gui

        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": str(i)},
                "action": "sound",
                "sound": "message",
            }
            for i in range(config.MAX_RULES)
        ]
        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": rules}}}
        )
        with mock.patch.object(window, "_save") as save:
            window._add_app_rule("warp")
        save.assert_not_called()
        self.assertEqual(len(window._get_app_rules("warp")), config.MAX_RULES)

    def test_set_app_rule_updates_fields(self):
        from notify_sound import gui

        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": "message",
            }
        ]
        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": rules}}}
        )
        with mock.patch.object(window, "_save") as save:
            window._set_app_rule("warp", 0, "urgency", "eq", "2", "sound", "bell")
        save.assert_called_once_with()
        rule = window._get_app_rules("warp")[0]
        self.assertEqual(rule["match"]["field"], "urgency")
        self.assertEqual(rule["match"]["op"], "eq")
        self.assertEqual(rule["match"]["value"], 2)
        self.assertIsInstance(rule["match"]["value"], int)
        self.assertEqual(rule["action"], "sound")
        self.assertEqual(rule["sound"], "bell")

    def test_set_app_rule_silence_no_sound(self):
        from notify_sound import gui

        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": "message",
            }
        ]
        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": rules}}}
        )
        with mock.patch.object(window, "_save") as save:
            window._set_app_rule("warp", 0, "body", "contains", "x", "silence", None)
        save.assert_called_once_with()
        rule = window._get_app_rules("warp")[0]
        self.assertEqual(rule["action"], "silence")
        self.assertNotIn("sound", rule)

    def test_remove_app_rule(self):
        from notify_sound import gui

        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": "a"},
                "action": "sound",
                "sound": "message",
            },
            {
                "match": {"field": "body", "op": "contains", "value": "b"},
                "action": "sound",
                "sound": "bell",
            },
        ]
        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": rules}}}
        )
        with mock.patch.object(window, "_save") as save:
            window._remove_app_rule("warp", 0)
        save.assert_called_once_with()
        remaining = window._get_app_rules("warp")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["match"]["value"], "b")

    def test_rule_changes_persist_via_save(self):
        from notify_sound import gui

        rules = [
            {
                "match": {"field": "body", "op": "contains", "value": "x"},
                "action": "sound",
                "sound": "message",
            }
        ]
        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": rules}}}
        )
        with mock.patch.object(config, "save_config") as save_config:
            window._set_app_rule("warp", 0, "body", "contains", "y", "sound", "bell")
        save_config.assert_called_once()
        saved = save_config.call_args.args[0]
        self.assertEqual(saved["apps"]["warp"]["rules"][0]["match"]["value"], "y")
        self.assertEqual(saved["apps"]["warp"]["rules"][0]["sound"], "bell")

    def test_normalize_rule_value_urgency_eq_to_int(self):
        from notify_sound import gui

        window = self._rules_window({"apps": {}})
        value = window._normalize_rule_value("urgency", "eq", "2")
        self.assertEqual(value, 2)
        self.assertIsInstance(value, int)

    def test_normalize_rule_value_urgency_eq_invalid_keeps_string(self):
        from notify_sound import gui

        window = self._rules_window({"apps": {}})
        self.assertEqual(window._normalize_rule_value("urgency", "eq", "5"), "5")
        self.assertEqual(
            window._normalize_rule_value("urgency", "eq", "abc"), "abc"
        )

    def test_normalize_rule_value_non_urgency_keeps_string(self):
        from notify_sound import gui

        window = self._rules_window({"apps": {}})
        self.assertEqual(
            window._normalize_rule_value("body", "contains", "x"), "x"
        )

    def test_app_row_contains_rules_button(self):
        from notify_sound import gui

        cfg = {"apps": {"warp": {"enabled": True, "sound": None}}}
        window = self._row_window(cfg)
        entry = window.app_rows["warp"]
        self.assertIn("rules_button", entry)
        self.assertIsInstance(entry["rules_button"], gui.Gtk.Button)

    def test_rules_button_tooltip(self):
        from notify_sound import gui

        cfg = {"apps": {"warp": {"enabled": True, "sound": None}}}
        window = self._row_window(cfg)
        button = window.app_rows["warp"]["rules_button"]
        tooltip = button.get_tooltip_text()
        self.assertIsNotNone(tooltip)
        self.assertIn("reglas", tooltip.lower())

    def test_rule_field_options_include_titulo_not_resumen(self):
        from notify_sound import gui

        labels = [label for _, label in gui.RULE_FIELDS]
        self.assertIn("Título", labels)
        self.assertNotIn("Resumen", labels)
        self.assertIn(("summary", "Título"), gui.RULE_FIELDS)

    def test_rule_field_options_exclude_desktop_entry(self):
        from notify_sound import gui

        values = [value for value, _ in gui.RULE_FIELDS]
        labels = [label for _, label in gui.RULE_FIELDS]
        self.assertNotIn("desktop-entry", values)
        self.assertNotIn("Entrada desktop", labels)

    def test_rule_op_options_include_starts_with_ends_with(self):
        from notify_sound import gui

        self.assertIn(("starts_with", "Empieza con"), gui.RULE_OPS)
        self.assertIn(("ends_with", "Termina con"), gui.RULE_OPS)

    def test_rule_field_options_count(self):
        from notify_sound import gui

        self.assertEqual(len(gui.RULE_FIELDS), 3)
        self.assertEqual(
            [label for _, label in gui.RULE_FIELDS],
            ["Cuerpo", "Título", "Urgencia"],
        )

    def test_rule_op_options_count(self):
        from notify_sound import gui

        self.assertEqual(len(gui.RULE_OPS), 5)
        self.assertEqual(
            [label for _, label in gui.RULE_OPS],
            ["Contiene", "Regex", "Igual", "Empieza con", "Termina con"],
        )

    def test_rule_value_dropdown_shown_when_field_urgency(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "urgency", "op": "eq", "value": 2},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        widgets = row.widgets
        self.assertIsInstance(widgets["value"], gui.Gtk.Entry)
        self.assertIsInstance(widgets["urgency"], gui.Gtk.DropDown)
        self.assertFalse(widgets["value"].get_visible())
        self.assertTrue(widgets["urgency"].get_visible())
        self.assertEqual(widgets["urgency"].get_selected(), 2)

    def test_rule_value_entry_shown_when_field_body(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "body", "op": "contains", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        widgets = row.widgets
        self.assertIsInstance(widgets["value"], gui.Gtk.Entry)
        self.assertIsInstance(widgets["urgency"], gui.Gtk.DropDown)
        self.assertTrue(widgets["value"].get_visible())
        self.assertFalse(widgets["urgency"].get_visible())
        self.assertEqual(widgets["value"].get_text(), "x")

    def test_rule_value_dropdown_options_are_baja_normal_critica(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "urgency", "op": "eq", "value": 1},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        widgets = row.widgets
        model = widgets["urgency"].get_model()
        labels = [model.get_string(i) for i in range(model.get_n_items())]
        self.assertEqual(labels, ["Baja", "Normal", "Crítica"])

    def test_rule_value_switches_to_dropdown_when_field_changes_to_urgency(self):
        from notify_sound import gui

        rule = {
            "match": {"field": "body", "op": "eq", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": [rule]}}}
        )
        rules_box = gui.Gtk.ListBox()
        row = window._build_rule_row("warp", rule, rules_box)
        rules_box.append(row)
        widgets = row.widgets
        with mock.patch.object(window, "_save"):
            widgets["field"].set_selected(window._rule_field_index("urgency"))
        self.assertFalse(widgets["value"].get_visible())
        self.assertTrue(widgets["urgency"].get_visible())
        self.assertEqual(widgets["urgency"].get_selected(), 1)
        saved = window._get_app_rules("warp")[0]["match"]
        self.assertEqual(saved["field"], "urgency")
        self.assertEqual(saved["value"], 1)
        self.assertIsInstance(saved["value"], int)

    def test_rule_value_int_converts_to_string_when_leaving_urgency(self):
        from notify_sound import gui

        rule = {
            "match": {"field": "urgency", "op": "eq", "value": 2},
            "action": "sound",
            "sound": "message",
        }
        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": [rule]}}}
        )
        rules_box = gui.Gtk.ListBox()
        row = window._build_rule_row("warp", rule, rules_box)
        rules_box.append(row)
        widgets = row.widgets
        with mock.patch.object(window, "_save"):
            widgets["field"].set_selected(window._rule_field_index("body"))
        self.assertTrue(widgets["value"].get_visible())
        self.assertFalse(widgets["urgency"].get_visible())
        self.assertEqual(widgets["value"].get_text(), "2")
        saved = window._get_app_rules("warp")[0]["match"]
        self.assertEqual(saved["field"], "body")
        self.assertEqual(saved["value"], "2")
        self.assertIsInstance(saved["value"], str)

    def test_op_dropdown_disabled_when_field_urgency(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "urgency", "op": "eq", "value": 2},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        widgets = row.widgets
        self.assertFalse(widgets["op"].get_sensitive())

    def test_op_dropdown_enabled_when_field_body(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "body", "op": "contains", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        widgets = row.widgets
        self.assertTrue(widgets["op"].get_sensitive())

    def test_op_dropdown_forced_to_eq_when_field_urgency(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "urgency", "op": "contains", "value": 2},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        widgets = row.widgets
        self.assertEqual(widgets["op"].get_selected(), window._rule_op_index("eq"))

    def test_op_forced_to_eq_when_field_changes_to_urgency(self):
        from notify_sound import gui

        rule = {
            "match": {"field": "body", "op": "contains", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": [rule]}}}
        )
        rules_box = gui.Gtk.ListBox()
        row = window._build_rule_row("warp", rule, rules_box)
        rules_box.append(row)
        widgets = row.widgets
        with mock.patch.object(window, "_save"):
            widgets["field"].set_selected(window._rule_field_index("urgency"))
        self.assertFalse(widgets["op"].get_sensitive())
        self.assertEqual(widgets["op"].get_selected(), window._rule_op_index("eq"))
        saved = window._get_app_rules("warp")[0]["match"]
        self.assertEqual(saved["op"], "eq")

    def test_op_dropdown_restored_when_changing_from_urgency_to_body(self):
        from notify_sound import gui

        rule = {
            "match": {"field": "urgency", "op": "eq", "value": 2},
            "action": "sound",
            "sound": "message",
        }
        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": [rule]}}}
        )
        rules_box = gui.Gtk.ListBox()
        row = window._build_rule_row("warp", rule, rules_box)
        rules_box.append(row)
        widgets = row.widgets
        self.assertFalse(widgets["op"].get_sensitive())
        with mock.patch.object(window, "_save"):
            widgets["field"].set_selected(window._rule_field_index("body"))
        self.assertTrue(widgets["op"].get_sensitive())
        saved = window._get_app_rules("warp")[0]["match"]
        self.assertEqual(saved["field"], "body")

    def _three_rules(self):
        return [
            {
                "match": {"field": "body", "op": "contains", "value": value},
                "action": "sound",
                "sound": "message",
            }
            for value in ("a", "b", "c")
        ]

    def test_move_app_rule_moves_rule_down(self):
        from notify_sound import gui

        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        with mock.patch.object(window, "_save"):
            window._move_app_rule("warp", 0, 2)
        values = [r["match"]["value"] for r in window._get_app_rules("warp")]
        self.assertEqual(values, ["b", "c", "a"])

    def test_move_app_rule_moves_rule_up(self):
        from notify_sound import gui

        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        with mock.patch.object(window, "_save"):
            window._move_app_rule("warp", 2, 0)
        values = [r["match"]["value"] for r in window._get_app_rules("warp")]
        self.assertEqual(values, ["c", "a", "b"])

    def test_move_app_rule_persists_via_save(self):
        from notify_sound import gui

        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        with mock.patch.object(config, "save_config") as save_config:
            window._move_app_rule("warp", 0, 2)
        save_config.assert_called_once()
        saved = save_config.call_args.args[0]
        values = [r["match"]["value"] for r in saved["apps"]["warp"]["rules"]]
        self.assertEqual(values, ["b", "c", "a"])

    def test_move_app_rule_same_index_does_not_save(self):
        from notify_sound import gui

        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        with mock.patch.object(window, "_save") as save:
            window._move_app_rule("warp", 1, 1)
        save.assert_not_called()
        values = [r["match"]["value"] for r in window._get_app_rules("warp")]
        self.assertEqual(values, ["a", "b", "c"])

    def test_move_app_rule_out_of_range_does_not_save(self):
        from notify_sound import gui

        window = self._rules_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        with mock.patch.object(window, "_save") as save:
            window._move_app_rule("warp", 0, 5)
            window._move_app_rule("warp", 5, 0)
        save.assert_not_called()
        values = [r["match"]["value"] for r in window._get_app_rules("warp")]
        self.assertEqual(values, ["a", "b", "c"])

    def test_rule_row_has_drag_source(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "body", "op": "contains", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        handle = row.widgets["handle"]
        controllers = handle.observe_controllers()
        self.assertTrue(
            any(isinstance(c, gui.Gtk.DragSource) for c in controllers)
        )

    def test_rule_row_has_no_drag_source(self):
        """El DragSource vive en el handle, no en el row: los widgets
        interactivos (DropDown, Entry, Button) no deben capturar el drag."""
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "body", "op": "contains", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        controllers = row.observe_controllers()
        self.assertFalse(
            any(isinstance(c, gui.Gtk.DragSource) for c in controllers)
        )

    def test_rule_drag_prepare_uses_parent_row_index(self):
        """El prepare del drag empaqueta el indice del row padre del handle."""
        from notify_sound import gui

        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        rules_box = gui.Gtk.ListBox()
        for rule in window._get_app_rules("warp"):
            rules_box.append(window._build_rule_row("warp", rule, rules_box))
        row = rules_box.get_row_at_index(1)
        handle = row.widgets["handle"]
        drag_source = next(
            c for c in handle.observe_controllers()
            if isinstance(c, gui.Gtk.DragSource)
        )
        with mock.patch.object(gui.Gdk, "ContentProvider") as provider:
            provider.new_for_value.return_value = "provider"
            result = window._on_rule_drag_prepare(drag_source, 0, 0)
        self.assertEqual(result, "provider")
        value = provider.new_for_value.call_args.args[0]
        self.assertEqual(value.get_int(), 1)

    def test_rule_drop_reorders_and_rebuilds(self):
        """El drop reordena la regla origen al indice de la fila destino."""
        from notify_sound import gui

        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        rules_box = gui.Gtk.ListBox()
        for rule in window._get_app_rules("warp"):
            rules_box.append(window._build_rule_row("warp", rule, rules_box))
        target_row = rules_box.get_row_at_index(2)
        drop_target = next(
            c for c in target_row.observe_controllers()
            if isinstance(c, gui.Gtk.DropTarget)
        )
        with mock.patch.object(window, "_save"):
            result = window._on_rule_drop(drop_target, 0, 0, 0, "warp", rules_box)
        self.assertTrue(result)
        values = [r["match"]["value"] for r in window._get_app_rules("warp")]
        self.assertEqual(values, ["b", "c", "a"])

    def test_rule_drop_same_index_returns_false(self):
        """Soltar sobre la misma fila no reordena ni guarda."""
        from notify_sound import gui

        window = self._row_window(
            {"apps": {"warp": {"enabled": True, "rules": self._three_rules()}}}
        )
        rules_box = gui.Gtk.ListBox()
        for rule in window._get_app_rules("warp"):
            rules_box.append(window._build_rule_row("warp", rule, rules_box))
        target_row = rules_box.get_row_at_index(1)
        drop_target = next(
            c for c in target_row.observe_controllers()
            if isinstance(c, gui.Gtk.DropTarget)
        )
        with mock.patch.object(window, "_save") as save:
            result = window._on_rule_drop(drop_target, 1, 0, 0, "warp", rules_box)
        self.assertFalse(result)
        save.assert_not_called()
        values = [r["match"]["value"] for r in window._get_app_rules("warp")]
        self.assertEqual(values, ["a", "b", "c"])

    def test_rule_row_has_drop_target(self):
        from notify_sound import gui

        window = self._row_window({"apps": {"warp": {"enabled": True}}})
        rule = {
            "match": {"field": "body", "op": "contains", "value": "x"},
            "action": "sound",
            "sound": "message",
        }
        row = window._build_rule_row("warp", rule, gui.Gtk.ListBox())
        controllers = row.observe_controllers()
        self.assertTrue(
            any(isinstance(c, gui.Gtk.DropTarget) for c in controllers)
        )


class GuiDebounceTests(unittest.TestCase):
    """Tests del control anti-ráfaga (DEB-001)."""

    def _built_window(self, cfg):
        """Ventana con `_build_ui` ejecutado (widgets GTK reales).

        `Gtk.Widget.__init__` inicializa el GObject base sin pasar por
        `NotifyWindow.__init__` (que leería config real y arrancaría
        timers); `_build_ui` construye la UI completa sin display.
        """
        from notify_sound import gui

        window = gui.NotifyWindow.__new__(gui.NotifyWindow)
        gui.Gtk.Widget.__init__(window)
        window.cfg = cfg
        window.theme_ids = ["message"]
        window.app_rows = {}
        window.custom_rows = {}
        window._rebuilding = False
        with mock.patch.object(
            config, "load_state", return_value={"apps_seen": [], "app_meta": {}}
        ):
            window._build_ui()
        return window

    def test_debounce_spin_initializes_from_config(self):
        from notify_sound import gui

        cfg = {
            "enabled": True,
            "sound": "message",
            "custom_sounds": [],
            "autostart": True,
            "debounce_window": 3.5,
            "apps": {},
        }
        window = self._built_window(cfg)
        self.assertIsInstance(window.debounce_spin, gui.Gtk.SpinButton)
        self.assertEqual(window.debounce_spin.get_value(), 3.5)
        adjustment = window.debounce_spin.get_adjustment()
        self.assertEqual(adjustment.get_lower(), 0)
        self.assertEqual(adjustment.get_upper(), 60)

    def test_debounce_spin_defaults_to_2_0_when_missing(self):
        cfg = {
            "enabled": True,
            "sound": "message",
            "custom_sounds": [],
            "autostart": True,
            "apps": {},
        }
        window = self._built_window(cfg)
        self.assertEqual(window.debounce_spin.get_value(), 2.0)

    def test_debounce_change_persists_and_saves(self):
        from notify_sound import gui

        cfg = {"debounce_window": 2.0}
        window = _bare_window(cfg)
        spin = mock.Mock()
        spin.get_value.return_value = 4.5
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_debounce_changed(window, spin)
        save.assert_called_once_with()
        self.assertEqual(cfg["debounce_window"], 4.5)

    def test_debounce_zero_is_valid(self):
        from notify_sound import gui

        cfg = {"debounce_window": 2.0}
        window = _bare_window(cfg)
        spin = mock.Mock()
        spin.get_value.return_value = 0.0
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_debounce_changed(window, spin)
        save.assert_called_once_with()
        self.assertEqual(cfg["debounce_window"], 0.0)

    def test_debounce_saves_float_not_int(self):
        from notify_sound import gui

        cfg = {"debounce_window": 2.0}
        window = _bare_window(cfg)
        spin = mock.Mock()
        spin.get_value.return_value = 2  # int crudo del widget
        with mock.patch.object(window, "_save") as save:
            gui.NotifyWindow._on_debounce_changed(window, spin)
        save.assert_called_once_with()
        self.assertIsInstance(cfg["debounce_window"], float)
        self.assertEqual(cfg["debounce_window"], 2.0)


class NotifySendTests(unittest.TestCase):
    """Tests del helper de envío D-Bus (`notify_sound/notify.py`)."""

    DEST = "org.freedesktop.Notifications"
    PATH = "/org/freedesktop/Notifications"
    INTERFACE = "org.freedesktop.Notifications"
    METHOD = "Notify"

    def _fake_gio(self, bus=None):
        """Fake de `notify.Gio` con `bus_get_sync` devolviendo `bus`."""
        fake_gio = mock.Mock()
        fake_gio.BusType.SESSION = "session"
        fake_gio.DBusCallFlags.NONE = 0
        fake_gio.bus_get_sync.return_value = bus
        return fake_gio

    def _assert_notify_call(self, bus, body):
        """Verifica que `bus.call_sync` se invocó con los parámetros exactos."""
        bus.call_sync.assert_called_once()
        args = bus.call_sync.call_args.args
        self.assertEqual(args[0], self.DEST)
        self.assertEqual(args[1], self.PATH)
        self.assertEqual(args[2], self.INTERFACE)
        self.assertEqual(args[3], self.METHOD)
        params = args[4]
        self.assertIsInstance(params, GLib.Variant)
        self.assertEqual(
            params.unpack(),
            (
                "notify-send",
                0,
                "",
                "NotifySound",
                body,
                [],
                {"urgency": 1, "x-notify-sound-done": "1"},
                -1,
            ),
        )
        self.assertEqual(
            args[5:],
            (None, 0, notify._DBUS_CALL_TIMEOUT_MS, None),
        )

    def test_send_uses_exact_dbus_command_with_default_message(self):
        bus = mock.Mock()
        with mock.patch.object(notify, "Gio", self._fake_gio(bus)):
            ok, error = notify.send_done_notification()
        self.assertTrue(ok)
        self.assertIsNone(error)
        self._assert_notify_call(bus, "Comando finalizado")

    def test_custom_message_is_passed_as_body(self):
        bus = mock.Mock()
        with mock.patch.object(notify, "Gio", self._fake_gio(bus)):
            ok, _ = notify.send_done_notification("build completo")
        self.assertTrue(ok)
        self._assert_notify_call(bus, "build completo")

    def test_blank_message_uses_default(self):
        bus = mock.Mock()
        with mock.patch.object(notify, "Gio", self._fake_gio(bus)):
            ok, _ = notify.send_done_notification("   ")
        self.assertTrue(ok)
        self._assert_notify_call(bus, "Comando finalizado")

    def test_empty_message_uses_default(self):
        bus = mock.Mock()
        with mock.patch.object(notify, "Gio", self._fake_gio(bus)):
            ok, _ = notify.send_done_notification("")
        self.assertTrue(ok)
        self._assert_notify_call(bus, "Comando finalizado")

    def test_missing_session_bus_returns_failure_without_running(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                notify, "Gio", self._fake_gio()
            ) as fake_gio:
                ok, error = notify.send_done_notification()
        self.assertFalse(ok)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", error)
        fake_gio.bus_get_sync.assert_not_called()

    def test_bus_connection_failure_returns_failure(self):
        fake_gio = self._fake_gio()
        fake_gio.bus_get_sync.side_effect = GLib.Error.new_literal(
            Gio.io_error_quark(), "no hay bus", 0
        )
        with mock.patch.object(notify, "Gio", fake_gio):
            ok, error = notify.send_done_notification()
        self.assertFalse(ok)
        self.assertIn("conectar al bus", error)

    def test_call_sync_error_returns_failure(self):
        bus = mock.Mock()
        bus.call_sync.side_effect = GLib.Error.new_literal(
            Gio.io_error_quark(), "boom", 0
        )
        with mock.patch.object(notify, "Gio", self._fake_gio(bus)):
            ok, error = notify.send_done_notification()
        self.assertFalse(ok)
        self.assertIn("falló", error)

    def test_timeout_returns_failure(self):
        bus = mock.Mock()
        bus.call_sync.side_effect = GLib.Error.new_literal(
            Gio.io_error_quark(),
            "GDBus.Error:org.freedesktop.DBus.Error.Timeout: "
            "Activatable service timed out",
            0,
        )
        with mock.patch.object(notify, "Gio", self._fake_gio(bus)):
            ok, error = notify.send_done_notification()
        self.assertFalse(ok)
        self.assertIn("falló", error)


class DoneSubcommandTests(unittest.TestCase):
    """Tests del subcomando `done` (`notify_sound/cli.py` + entrypoint)."""

    def _load_entrypoint(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader

        path = str(ROOT / "notify-sound")
        loader = SourceFileLoader("notify_sound_entrypoint", path)
        spec = importlib.util.spec_from_file_location(
            "notify_sound_entrypoint", path, loader=loader
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fallback_plays_sound_when_daemon_absent(self):
        with mock.patch.object(
            notify, "send_done_notification", return_value=(True, None)
        ) as send, mock.patch.object(
            config, "is_running", return_value=False
        ), mock.patch.object(
            config, "load_config", return_value={"sound": "message", "apps": {}}
        ), mock.patch.object(player, "play_choice") as play:
            code = cli.run_done()
        self.assertEqual(code, 0)
        send.assert_called_once_with(None)
        play.assert_called_once_with("message")

    def test_fallback_uses_app_sound_from_config(self):
        with mock.patch.object(
            notify, "send_done_notification", return_value=(True, None)
        ), mock.patch.object(config, "is_running", return_value=False), mock.patch.object(
            config,
            "load_config",
            return_value={
                "sound": "message",
                "apps": {
                    "notify-sound": {"enabled": True, "sound": "/tmp/custom.wav"}
                },
            },
        ), mock.patch.object(player, "play_choice") as play:
            code = cli.run_done()
        self.assertEqual(code, 0)
        play.assert_called_once_with("/tmp/custom.wav")

    def test_no_direct_play_when_daemon_running(self):
        with mock.patch.object(
            notify, "send_done_notification", return_value=(True, None)
        ), mock.patch.object(config, "is_running", return_value=True), mock.patch.object(
            config, "load_config"
        ) as load, mock.patch.object(player, "play_choice") as play:
            code = cli.run_done("build completo")
        self.assertEqual(code, 0)
        play.assert_not_called()
        load.assert_not_called()

    def test_custom_message_is_forwarded_to_send(self):
        with mock.patch.object(
            notify, "send_done_notification", return_value=(True, None)
        ) as send, mock.patch.object(config, "is_running", return_value=True):
            code = cli.run_done("build completo")
        self.assertEqual(code, 0)
        send.assert_called_once_with("build completo")

    def test_default_message_when_none(self):
        with mock.patch.object(
            notify, "send_done_notification", return_value=(True, None)
        ) as send, mock.patch.object(config, "is_running", return_value=True):
            code = cli.run_done()
        self.assertEqual(code, 0)
        send.assert_called_once_with(None)

    def test_send_failure_prints_error_and_returns_1(self):
        stderr = io.StringIO()
        with mock.patch.object(
            notify,
            "send_done_notification",
            return_value=(False, "NotifySound: boom"),
        ) as send, mock.patch.object(config, "is_running") as running, mock.patch.object(
            player, "play_choice"
        ) as play, mock.patch.object(sys, "stderr", stderr):
            code = cli.run_done()
        self.assertEqual(code, 1)
        self.assertIn("boom", stderr.getvalue())
        send.assert_called_once_with(None)
        running.assert_not_called()
        play.assert_not_called()

    def test_entrypoint_parses_done_before_flags(self):
        module = self._load_entrypoint()
        with mock.patch.object(
            sys, "argv", ["notify-sound", "done", "build", "completo"]
        ), mock.patch("notify_sound.cli.run_done", return_value=0) as run_done:
            code = module.main()
        self.assertEqual(code, 0)
        run_done.assert_called_once_with("build completo")

    def test_entrypoint_done_without_message_passes_none(self):
        module = self._load_entrypoint()
        with mock.patch.object(sys, "argv", ["notify-sound", "done"]), mock.patch(
            "notify_sound.cli.run_done", return_value=0
        ) as run_done:
            code = module.main()
        self.assertEqual(code, 0)
        run_done.assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()

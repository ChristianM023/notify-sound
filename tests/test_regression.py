import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from notify_sound import config, daemon, player, sounds


ROOT = Path(__file__).resolve().parents[1]


def _hint_entry(hint):
    if isinstance(hint, tuple):
        key, value = hint
    else:
        key, value = hint, "message"
    return (
        "      dict entry(\n"
        f'         string "{key}"\n'
        f'         variant string "{value}"\n'
        "      )\n"
    )


def notification(app_name, hints=(), trailing_blank=True, body="Body"):
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
            '   string "Summary"\n',
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
                "no_duplicate": None,
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
            self.assertIsInstance(loaded["no_duplicate"], bool)
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
            "no_duplicate": True,
            "autostart": True,
            "apps": {
                "warp": {
                    "enabled": True,
                    "sound": "/tmp/notify-sound-test-I-Feel-Good.wav",
                },
                "Telegram Desktop": {
                    "enabled": True,
                    "sound": "/tmp/notify-sound-test-Whistle.wav",
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
        play.assert_called_once_with("message")
        self.assertEqual(instance.seen, {"notify-send"})

    def test_two_notifications_are_both_processed(self):
        payload = notification("first") + notification(
            "second", trailing_blank=False
        )
        instance, monitor = self.make_daemon(payload)
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        self.assertEqual(play.call_count, 2)
        self.assertEqual(instance.seen, {"first", "second"})

    def test_sound_hints_are_not_duplicated(self):
        for hint in ("sound-name", "sound-file"):
            instance, monitor = self.make_daemon(
                notification("with-hint", hints=(hint,), trailing_blank=False)
            )
            with mock.patch.object(player, "play_choice") as play:
                instance._reader(monitor)
            play.assert_not_called()

    def test_sound_hint_is_played_when_no_duplicate_is_disabled(self):
        self.write_config({"no_duplicate": False})
        instance, monitor = self.make_daemon(
            notification("with-hint", hints=("sound-name",), trailing_blank=False)
        )
        with mock.patch.object(player, "play_choice") as play:
            instance._reader(monitor)
        play.assert_called_once_with("message")

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
        play.assert_called_once_with("message")

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
        play.assert_called_once_with("message")
        self.assertEqual(instance.seen, {"org.gnome.Ptyxis"})

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
        play.assert_called_once_with("message")
        self.assertEqual(instance.seen, {"aimp"})

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
        self.assertEqual(instance.seen, {"short"})

        instance, monitor = self.make_daemon(
            notification(
                "short",
                hints=(("desktop-entry", long_value),),
                trailing_blank=False,
            )
        )
        with mock.patch.object(player, "play_choice"):
            instance._reader(monitor)
        self.assertEqual(instance.seen, {"short"})

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
        self.assertEqual(instance.seen, {"aimp"})

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
        self.assertEqual(instance.seen, {"aimp"})

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
        play.assert_called_once_with("message")
        self.assertEqual(instance.seen, {"aimp"})
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
        play.assert_called_once_with("message")
        self.assertEqual(instance.seen, {"aimp"})

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
        self.assertEqual(instance.seen, {"notify-send"})

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
        play.assert_called_once_with("message")
        self.assertEqual(instance.seen, {"aimp"})

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
        play.assert_called_once_with("message")

    def test_suppress_sound_is_always_silent(self):
        self.write_config({"no_duplicate": False})
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
        play.assert_called_once_with(app_sound)

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
        self.assertEqual(instance.seen, {"first", "second"})

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
                    "no_duplicate": True,
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

    def _bare_window(self, cfg):
        from notify_sound import gui

        window = gui.NotifyWindow.__new__(gui.NotifyWindow)
        window.cfg = cfg
        window.app_rows = {}
        window.apps_list = mock.Mock()
        window._rebuilding = False
        return window

    def test_display_name_prefers_alias_over_canonical(self):
        from notify_sound import gui

        window = self._bare_window({"apps": {"aimp": {"name": "AIMP"}}})
        self.assertEqual(window._display_name("aimp"), "AIMP")
        self.assertEqual(window._display_name("telegram"), "telegram")

    def test_app_rename_save_updates_config_and_label(self):
        from notify_sound import gui

        cfg = {"apps": {"aimp": {"enabled": True, "sound": None}}}
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)

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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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
        window = self._bare_window({"apps": {}})
        with mock.patch.object(window, "_merge_app_into") as merge:
            gui.NotifyWindow._on_merge_confirm(
                window, None, "songB", "aimp", popover
            )
        popover.popdown.assert_called_once_with()
        merge.assert_called_once_with("songB", "aimp")

    def test_merge_dialog_choice_cancel_does_nothing(self):
        from notify_sound import gui

        popover = mock.Mock()
        window = self._bare_window({"apps": {}})
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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

        window = self._bare_window({"apps": {"warp": {"enabled": True}}})
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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
        window = self._bare_window(cfg)
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

        cfg = {"apps": {"warp": {"enabled": True, "sound": None}}}
        window = self._bare_window(cfg)
        window.theme_ids = ["message"]
        window.apps_list = mock.Mock()
        captured = {}

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
        window = self._bare_window(cfg)
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

    def test_custom_sounds_multi_add_appends_all(self):
        from notify_sound import gui

        cfg = {"custom_sounds": ["/old.wav"]}
        window = self._bare_window(cfg)
        gfile_a = mock.Mock()
        gfile_a.get_path.return_value = "/new1.wav"
        gfile_b = mock.Mock()
        gfile_b.get_path.return_value = "/new2.wav"
        dialog = mock.Mock()
        dialog.open_multiple_finish.return_value = [gfile_a, gfile_b]
        with mock.patch.object(window, "_save") as save, \
             mock.patch.object(window, "_refresh_custom_list") as refresh, \
             mock.patch.object(window, "_rebuild_all_dropdowns") as rebuild:
            gui.NotifyWindow._on_add_custom_done(window, dialog, mock.Mock())
        save.assert_called_once_with()
        refresh.assert_called_once_with()
        rebuild.assert_called_once_with()
        self.assertIn("/new1.wav", cfg["custom_sounds"])
        self.assertIn("/new2.wav", cfg["custom_sounds"])
        self.assertIn("/old.wav", cfg["custom_sounds"])


if __name__ == "__main__":
    unittest.main()

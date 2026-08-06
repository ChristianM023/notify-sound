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

from notify_sound import config, daemon, player


ROOT = Path(__file__).resolve().parents[1]


def notification(app_name, hints=(), trailing_blank=True):
    hint_lines = "".join(
        "      dict entry(\n"
        f'         string "{hint}"\n'
        '         variant string "message"\n'
        "      )\n"
        for hint in hints
    )
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
            '   string "Body"\n',
            "   array [\n",
            "   ]\n",
            "   array [\n",
            hint_lines,
            "   ]\n",
            "   int32 -1\n",
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
        state = {"apps_seen": ["Telegram Desktop", "notify-send", "warp"]}
        Path(config.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(config.load_state(), state)

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


class DaemonTests(ConfigTests):
    def make_daemon(self, payload):
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

    def test_custom_prefix_generates_matching_autostart_and_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            prefix = root / "opt" / "test"
            env = os.environ.copy()
            env.update({"HOME": str(home), "PREFIX": str(prefix)})
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


if __name__ == "__main__":
    unittest.main()

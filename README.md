# NotifySound

Play sounds for desktop notifications that do not include one.

On GNOME (and other desktop environments built on `org.freedesktop.Notifications`),
a notification only makes a sound when the app that sent it explicitly includes a
`sound-name` or `sound-file` hint. Most apps never do — Warp, terminals, IDE
builds, cron-like tools — so their notifications stay silent.

NotifySound is a small daemon that watches the notification bus and plays a
sound of your choice whenever a notification that has no sound of its own
arrives. It also ships a simple GTK4 app to configure everything.

## How it works

- The daemon spawns `dbus-monitor` (part of the `dbus` package, present on every
  desktop) with an eavesdropping match rule and parses its output in a reader
  thread. This is the only reliable way to observe `Notify` method calls from
  Python: `Gio.DBusConnection` handles `AddMatch` locally without ever reaching
  the bus daemon, and `BecomeMonitor` is rejected for ordinary connections.
- Each notification is processed **immediately** (no waiting for the next one):
  a message ends at its top-level `int32 <timeout>` line — the last argument of
  every `Notify` call. Blank lines are deliberately **not** used as message
  separators, because string values containing `\n\n` (chat apps, emails)
  print as physical blank lines *inside* a message and would cut it in half.
- Notifications that carry `sound-file`/`sound-name` hints are left untouched
  when **"no duplicate"** is enabled (Telegram keeps its own sound, for
  example). With it disabled both sounds play on purpose.
- Notifications carrying the **`suppress-sound`** hint are **always** silent:
  the app declared that it manages its own sound (Chromium-based browsers send
  this for PWAs/web pages that play audio themselves).
- Re-emissions from gnome-shell (identified by the `x-shell-sender` hint) are
  discarded so each notification plays exactly once.
- For everything else it plays the sound configured in the GUI
  (`canberra-gtk-play`, the freedesktop sound system).
- Apps that send notifications are collected automatically so you can tune
  each one individually (enable/disable, per-app sound).

## Requirements

- Python 3.10+
- PyGObject (`python3-gi`)
- `canberra-gtk-play` (`gnome-session-canberra`)
- GTK 4.10+ (for the GUI only)
- `dbus-monitor` (part of the `dbus` package, present on every desktop)

Install on Ubuntu/Debian:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gnome-session-canberra dbus
```

## Install

```sh
git clone https://github.com/ChristianM023/notify-sound.git
cd notify-sound
./install.sh
```

This installs to `~/.local/bin`, adds a launcher and enables autostart
(the daemon starts with your session). Use `--no-autostart` to skip that.
A custom `PREFIX=/some/path ./install.sh` installs elsewhere; the wrapper
exports `NOTIFY_SOUND_BIN` so autostart and the GUI always use the right
binary.

### Debian package

Ubuntu/Debian users can install the release package without cloning the
repository:

```sh
sudo dpkg -i ./notify-sound_0.1.7_all.deb
sudo apt-get -f install
notify-sound
```

The package installs the program under `/usr`, does not enable a daemon
automatically, and includes a user-level systemd unit template:

```sh
mkdir -p ~/.config/systemd/user
cp /usr/share/notify-sound/notify-sound.service ~/.config/systemd/user/
systemctl --user enable --now notify-sound
```

### systemd (alternative to autostart)

```sh
mkdir -p ~/.config/systemd/user
cp ~/.local/share/notify-sound/notify-sound.service ~/.config/systemd/user/
systemctl --user enable --now notify-sound
```

For a custom `PREFIX`, copy the generated unit from
`$PREFIX/share/notify-sound/notify-sound.service` instead.

### Uninstall

```sh
rm -rf ~/.local/share/notify-sound ~/.local/bin/notify-sound
rm ~/.local/share/applications/notify-sound.desktop
rm ~/.config/autostart/notify-sound.desktop
rm -rf ~/.config/notify-sound
```

## Usage

```sh
notify-sound            # open the settings GUI
notify-sound --daemon   # start the daemon
notify-sound --quit     # stop the daemon
```

### GUI features

- Master on/off switch
- **Autostart toggle**: start the daemon with your session (writes/removes
  `~/.config/autostart/notify-sound.desktop`)
- Global sound picker (theme sounds from the active sound theme) + **Test** button
- Add any number of **custom sound files**; they appear in the global and
  per-app sound pickers
- "No duplicate" mode: keep the app's own sound when it sends one
- Per-app enable/disable, per-app sound selection and per-app **Test** button
- Start/stop daemon buttons (the GUI is single-instance: launching it again
  focuses the existing window)

## Custom sound formats

- **OGG, WAV, FLAC:** played with `canberra-gtk-play` (no extra software).
- **MP3, M4A, AAC, etc.:** `canberra-gtk-play` cannot decode them, so
  NotifySound falls back to `gst-launch-1.0` (GStreamer), then `ffplay`,
  `mpv` or `mpg123`. Install at least one of them, e.g.:

  ```sh
  sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  ```

## Configuration

Stored in `~/.config/notify-sound/config.json`:

```json
{
  "enabled": true,
  "sound": "message",
  "custom_sounds": ["/path/to/my-sound.mp3"],
  "no_duplicate": true,
  "autostart": true,
  "apps": {
    "warp": { "enabled": true, "sound": null }
  }
}
```

`"sound"` is the current choice: a theme sound id (`message`,
`dialog-warning`, ...) or the path of one of the files in `custom_sounds`.
`"sound": null` inside an app means "inherit the global sound".

`~/.config/notify-sound/state.json` holds the auto-detected app list:

```json
{ "apps_seen": ["warp", "Telegram Desktop", "notify-send"] }
```

The app list is auto-detected, so entries are re-added when the app sends
a new notification.

## Troubleshooting

- **Nothing plays at all:** make sure `canberra-gtk-play` is installed and
  test it with `canberra-gtk-play -i message`. Verify the daemon is running
  with `notify-sound --quit` (it reports "not running") or check the GUI status.
- **Custom sound file (MP3, M4A...) does not play:** `canberra-gtk-play` only
  decodes OGG/WAV/FLAC. For other formats NotifySound falls back to
  `gst-launch-1.0` (GStreamer), then `ffplay`, `mpv` or `mpg123` — install at
  least one of them, e.g. `sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good`.
- **Notifications with their own sound are played twice:** enable
  "no_duplicate" in the GUI (disabled means both sounds play on purpose).
  If an app sends `suppress-sound`, NotifySound is silent by design.
- **A notification arrives but plays no sound:** if the app sends
  `suppress-sound` this is intentional (the app plays its own sound). If not,
  check the per-app toggle for that app in the GUI.
- **Sound plays only after the next notification:** update to 0.1.1 or later;
  NotifySound processes each D-Bus message immediately. If the player starts
  immediately but audio is delayed, report it with details on your shell and
  notification server.

## Development

### Layout

```
notify-sound              # CLI entrypoint: --daemon | --quit | --gui (default)
notify_sound/
├── config.py             # config/state/pidfile paths, autostart, JSON load/save
│                         # (validates types, tolerates corrupt files, migrates
│                         #  the pre-0.1.1 "custom_sound" key), instance lock
├── daemon.py             # dbus-monitor subprocesses, output parsers, play rules,
│                         # backoff restarts, SIGTERM handling
├── player.py             # playback: canberra for OGG/WAV/FLAC, fallback chain
│                         # (gst-launch-1.0 → ffplay → mpv → mpg123) for the rest
├── sounds.py             # discovers the active sound theme's available sounds
├── gui.py                # GTK4 settings app (single instance)
└── __init__.py           # __version__
tests/test_regression.py  # unittest regression suite
share/notify-sound.service   # systemd unit template
install.sh                # per-user installer (PREFIX-aware)
build-deb.sh              # Debian package builder
```

### Running the tests

```sh
python3 -m unittest discover -s tests -v
```

The suite uses the `notification()` and `gtk_notification()` helpers to build
realistic dbus-monitor output (including multi-line bodies and hints) and
asserts on what would be played/skipped. Add a test for every new behavior.

### Parser contract (read before touching `daemon.py`)

- The reader thread consumes dbus-monitor's stdout line by line. A standard
  `Notify` message starts at a line matching `_MESSAGE_HEADER_RE` and **ends
  at its top-level `int32 <n>` line** (`_TOP_LEVEL_INT32_RE`: exactly 3 spaces
  + `int32`). This is the Notify timeout argument — always the last argument,
  and the only top-level `int32` in a Notify message.
- GTK applications such as Ptyxis use `org.gtk.Notifications.AddNotification`.
  Those messages end at the closing bracket of their outer top-level `array`,
  outside quoted strings. Incomplete or oversized messages are discarded.
- Do **not** reintroduce blank-line or column-0 based message separation:
  string values with embedded newlines (`"a\n\nb"`) print as physical blank
  and unindented lines inside a message. This was the root cause of double
  playback for chat apps (v0.1.4).
- Framing markers and message headers are recognized only outside quoted
  strings, so notification content cannot imitate a terminator or header.
- gnome-shell re-sends every notification with `x-shell-sender` +
  `x-shell-sender-pid` hints; those blocks must be skipped.
- Playback rules, in order: master `enabled` off → nothing; `suppress-sound`
  hint → always nothing; per-app disabled → nothing; `sound-file`/`sound-name`
  hint with `no_duplicate` → nothing; otherwise play the app's choice or the
  global choice (a theme id or a custom file path).

### Daemon lifecycle

- Single instance: `config.acquire_instance_lock()` takes an `flock` on the
  permanent lock file (`$XDG_RUNTIME_DIR/notify-sound.pid.lock`, per-user
  fallback in `~/.cache/notify-sound/`), opened with `O_NOFOLLOW` and mode
  0600. The PID is written atomically to the adjacent `.pid` file.
- `dbus-monitor` is restarted with exponential backoff (1s → 30s) if it exits;
  the backoff resets after 5s of stability. If the binary is missing the
  daemon retries in the background instead of dying. It observes both
  `org.freedesktop.Notifications.Notify` and
  `org.gtk.Notifications.AddNotification`.
- `SIGTERM`/`SIGINT` stop the main loop, terminate the monitor and remove the
  pidfile. `--quit` sends `SIGTERM` to the pid in the pidfile.

### Release checklist

1. Bump `__version__` in `notify_sound/__init__.py`.
2. Add a Changelog entry below and update versioned release links.
3. Run the full test suite.
4. `./install.sh` to deploy, then restart the daemon
   (`notify-sound --quit && notify-sound --daemon`).
5. Merge through a PR with the required `tests` check.
6. Build the Debian package and its checksum:

   ```sh
   ./build-deb.sh
   sha256sum dist/notify-sound_X.Y.Z_all.deb \
     > dist/notify-sound_X.Y.Z_all.deb.sha256
   ```

7. Create and push the matching `vX.Y.Z` tag, create a draft GitHub release,
   upload the `.deb` and checksum, then publish the release.
8. Verify the release checksum and the successful GitHub Pages deployment.

## Changelog

- **0.1.7** — Debian package and checksum improvements, FLAC theme sounds,
  and a complete release checklist.
- **0.1.6** — security hardening: quote-aware bounded notification parsing,
  GTK notification support (including Ptyxis), private atomic config/state
  writes, bounded playback and decoder timeouts, safe sound/path validation,
  hardened installer/autostart, and pinned Pages actions.
- **0.1.5** — fix daemon shutdown: terminate/wait/kill ordering so `--quit`
  always stops the daemon and its `dbus-monitor` cleanly.
- **0.1.4** — fix multiline parsing: message end detected via the top-level
  `int32` timeout line instead of blank lines (multiline bodies caused double
  playback and hid hints). Respect the `suppress-sound` hint always (apps
  that manage their own sound stay silent — no more double sound with
  Chromium PWAs like WhatsApp Web).
- **0.1.3** — GUI releases its hold on window close, so the process exits
  instead of staying resident.
- **0.1.2** — GUI is single-instance: re-launching focuses the existing
  window instead of opening a new one.
- **0.1.1** — harden daemon and config: immediate parsing (no delay until the
  next notification), safe playback when `canberra-gtk-play` is missing,
  exponential restart backoff, atomic instance lock (no stale pidfiles, no
  `/tmp` collision), tolerant config parsing, PREFIX-aware install/autostart,
  playback fallback for MP3/other formats, regression suite.
- **0.1.0** — initial release: daemon, GTK4 GUI, theme/custom sounds,
  per-app config, autostart, install script.

## Why does this exist?

Background: GNOME plays notification sounds only when the notification carries a
sound hint (per the freedesktop notification spec). Relevant discussions:

- [gnome-shell maintainer explaining the behaviour](https://discourse.gnome.org/t/no-notification-sound/19933)
- Warp: [play_notification_sound is macOS-only](https://github.com/warpdotdev/warp/issues/8901) /
  [feature request: sound after long commands](https://github.com/warpdotdev/warp/issues/4155)

## License

MIT

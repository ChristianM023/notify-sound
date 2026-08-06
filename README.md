# NotifySound

Play sounds for desktop notifications that do not include one.

On GNOME (and other desktop environments built on `org.freedesktop.Notifications`),
a notification only makes a sound when the app that sent it explicitly includes a
`sound-name` or `sound-file` hint. Most apps never do — Warp, terminals, IDE
builds, cron-like tools — so their notifications stay silent.

NotifySound is a small daemon that watches the notification bus and plays a
sound of your choice whenever a notification without a sound hint arrives.
It also ships a simple GTK4 app to configure everything.

## How it works

- The daemon attaches a filter to the session D-Bus and listens for
  `org.freedesktop.Notifications.Notify` method calls.
- Notifications already carrying `sound-file`/`sound-name` hints are left
  untouched (no double sounds — Telegram keeps its own sound, for example).
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

### systemd (alternative to autostart)

```sh
mkdir -p ~/.config/systemd/user
cp ~/.local/share/notify-sound/notify-sound.service ~/.config/systemd/user/
systemctl --user enable --now notify-sound
```

For a custom `PREFIX`, copy the generated unit from
`$PREFIX/share/notify-sound/notify-sound.service` instead.

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
- Per-app enable/disable and per-app sound selection
- Start/stop daemon buttons

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
Apps seen by the daemon are listed in `~/.config/notify-sound/state.json`;
the app list is auto-detected, so entries are re-added when the app sends
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
- **Sound plays only after the next notification:** update to 0.1.1 or later;
  NotifySound processes each D-Bus message immediately. If the player starts
  immediately but audio is delayed, report it with details on your shell and
  notification server.

## Why does this exist?

Background: GNOME plays notification sounds only when the notification carries a
sound hint (per the freedesktop notification spec). Relevant discussions:

- [gnome-shell maintainer explaining the behaviour](https://discourse.gnome.org/t/no-notification-sound/19933)
- Warp: [play_notification_sound is macOS-only](https://github.com/warpdotdev/warp/issues/8901) /
  [feature request: sound after long commands](https://github.com/warpdotdev/warp/issues/4155)

## License

MIT

#!/usr/bin/env bash
set -euo pipefail
umask 022

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$SRC/dist}"
PYTHON="$(command -v python3 || true)"

if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  printf '%s\n' "python3 es necesario para leer la version." >&2
  exit 2
fi
if ! command -v dpkg-deb >/dev/null 2>&1; then
  printf '%s\n' "dpkg-deb es necesario para construir el paquete." >&2
  exit 2
fi

VERSION="$("$PYTHON" -c '
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"__version__\s*=\s*\"([^\"]+)\"", text)
if not match or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", match.group(1)):
    raise SystemExit("version invalida")
print(match.group(1))
' "$SRC/notify_sound/__init__.py")"
PACKAGE="notify-sound_${VERSION}_all.deb"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/notify-sound-deb.XXXXXX")"
PKGROOT="$BUILD_DIR/root"
OUTPUT="$OUT_DIR/$PACKAGE"
trap 'rm -rf "$BUILD_DIR"' EXIT

install -d -m 755 \
  "$PKGROOT/DEBIAN" \
  "$PKGROOT/usr/bin" \
  "$PKGROOT/usr/lib/notify-sound" \
  "$PKGROOT/usr/share/applications" \
  "$PKGROOT/usr/share/doc/notify-sound" \
  "$PKGROOT/usr/share/notify-sound"

cp -a "$SRC/notify_sound" "$PKGROOT/usr/lib/notify-sound/"
rm -rf "$PKGROOT/usr/lib/notify-sound/notify_sound/__pycache__"
install -m 755 "$SRC/notify-sound" \
  "$PKGROOT/usr/lib/notify-sound/notify-sound"

cat > "$PKGROOT/usr/bin/notify-sound" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
export NOTIFY_SOUND_BIN=/usr/bin/notify-sound
exec /usr/bin/python3 /usr/lib/notify-sound/notify-sound "$@"
EOF
chmod 755 "$PKGROOT/usr/bin/notify-sound"

cat > "$PKGROOT/usr/share/applications/notify-sound.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=NotifySound
Comment=Play sounds for silent notifications
Exec=/usr/bin/notify-sound
Icon=audio-volume-high
Terminal=false
Categories=Utility;AudioVideo;
EOF

cat > "$PKGROOT/usr/share/notify-sound/notify-sound.service" <<'EOF'
[Unit]
Description=NotifySound daemon
After=graphical-session.target

[Service]
ExecStart=/usr/bin/notify-sound --daemon
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=2
NoNewPrivileges=yes
PrivateTmp=yes
MemoryMax=256M
TasksMax=64

[Install]
WantedBy=default.target
EOF

install -m 644 "$SRC/README.md" "$PKGROOT/usr/share/doc/notify-sound/README.md"
install -m 644 "$SRC/LICENSE" "$PKGROOT/usr/share/doc/notify-sound/LICENSE"

cat > "$PKGROOT/DEBIAN/control" <<EOF
Package: notify-sound
Version: $VERSION
Section: sound
Priority: optional
Architecture: all
Maintainer: ChristianM023 <129190600+ChristianM023@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gnome-session-canberra, dbus
Recommends: gstreamer1.0-tools, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good
Suggests: ffmpeg, mpv, mpg123
Homepage: https://github.com/ChristianM023/notify-sound
Description: sounds for desktop notifications without one
 NotifySound watches the user notification bus and plays a configured sound
 for notifications that do not provide their own sound.
 .
 It includes a GTK4 settings application, per-application sound choices,
 custom sound files and a user-level systemd service template.
EOF

install -d -m 755 "$OUT_DIR"
rm -f "$OUTPUT"
dpkg-deb --build --root-owner-group "$PKGROOT" "$OUTPUT" >/dev/null
printf '%s\n' "$OUTPUT"

#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$PREFIX/share/notify-sound"
AUTOSTART=1

for arg in "$@"; do
  case "$arg" in
    --no-autostart) AUTOSTART=0 ;;
    -h|--help)
      echo "Uso: install.sh [--no-autostart]"
      echo "Instala NotifySound en ~/.local (usa PREFIX para otra ruta)."
      exit 0
      ;;
  esac
done

mkdir -p "$PREFIX/bin" "$PREFIX/share/applications"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$SRC/notify_sound" "$SRC/notify-sound" "$DEST/"

cat > "$PREFIX/bin/notify-sound" <<EOF
#!/usr/bin/env bash
exec python3 "$DEST/notify-sound" "\$@"
EOF
chmod +x "$PREFIX/bin/notify-sound"

cat > "$PREFIX/share/applications/notify-sound.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NotifySound
Comment=Play sounds for silent notifications
Exec=$PREFIX/bin/notify-sound
Icon=audio-volume-high
Terminal=false
Categories=Utility;AudioVideo;
EOF

if [ "$AUTOSTART" = "1" ] && \
   [ -f "$HOME/.config/notify-sound/config.json" ] && \
   grep -q '"autostart": *false' "$HOME/.config/notify-sound/config.json"; then
  echo "Autostart omitido (deshabilitado en la configuración)"
  AUTOSTART=0
fi

if [ "$AUTOSTART" = "1" ]; then
  mkdir -p "$HOME/.config/autostart"
  cat > "$HOME/.config/autostart/notify-sound.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NotifySound daemon
Comment=Play sounds for notifications that do not include one
Exec=$PREFIX/bin/notify-sound --daemon
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
EOF
  echo "Autostart habilitado (~/.config/autostart/notify-sound.desktop)"
fi

echo "Instalado en $PREFIX"
echo "  Ejecutable: $PREFIX/bin/notify-sound"
echo "  Lanzador:   $PREFIX/share/applications/notify-sound.desktop"
echo "Inicia la GUI con: notify-sound"

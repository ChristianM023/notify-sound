#!/usr/bin/env bash
set -euo pipefail
umask 077

PREFIX="${PREFIX:-$HOME/.local}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$PREFIX/share/notify-sound"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
AUTOSTART=1
PYTHON="$(command -v python3 || true)"

if [[ "$PREFIX" != /* || -z "$PYTHON" || "$PYTHON" != /* ]]; then
  printf '%s\n' "PREFIX debe ser absoluto y python3 debe estar disponible." >&2
  exit 2
fi
if [[ "$PREFIX" == *$'\n'* || "$PREFIX" == *$'\r'* || \
      "$PREFIX" == *'"'* || "$PREFIX" == *'\\'* || "$PREFIX" == *'%'* ]]; then
  printf '%s\n' "PREFIX contiene caracteres no permitidos." >&2
  exit 2
fi

desktop_exec() {
  printf '"%s"' "$1"
}

for arg in "$@"; do
  case "$arg" in
    --no-autostart) AUTOSTART=0 ;;
    -h|--help)
      echo "Uso: install.sh [--no-autostart]"
       echo "Instala NotifySound en ~/.local (usa PREFIX para otra ruta)."
       exit 0
       ;;
    *)
      printf 'Argumento no reconocido: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done

mkdir -p -m 700 "$PREFIX/bin" "$PREFIX/share/applications"
rm -rf "$DEST"
mkdir -p -m 700 "$DEST"
cp -r "$SRC/notify_sound" "$SRC/notify-sound" "$DEST/"

{
  printf '%s\n' \
    '[Unit]' \
    'Description=NotifySound daemon' \
    'After=graphical-session.target' \
    '' \
    '[Service]'
  printf 'ExecStart="%s" --daemon\n' "$PREFIX/bin/notify-sound"
  printf '%s\n' \
    'Restart=on-failure' \
    'RestartSec=2' \
    'NoNewPrivileges=yes' \
    'PrivateTmp=yes' \
    'MemoryMax=256M' \
    'TasksMax=64' \
    '' \
    '[Install]' \
    'WantedBy=default.target'
} > "$DEST/notify-sound.service"

{
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
  printf '%s\n' 'export PATH=/usr/local/bin:/usr/bin:/bin'
  printf 'export NOTIFY_SOUND_BIN=%q\n' "$PREFIX/bin/notify-sound"
  printf 'exec %q %q "$@"\n' "$PYTHON" "$DEST/notify-sound"
} > "$PREFIX/bin/notify-sound"
chmod 700 "$PREFIX/bin/notify-sound"

{
  printf '%s\n' \
    '[Desktop Entry]' \
    'Type=Application' \
    'Name=NotifySound' \
    'Comment=Play sounds for silent notifications'
  printf 'Exec=%s\n' "$(desktop_exec "$PREFIX/bin/notify-sound")"
  printf '%s\n' \
    'Icon=audio-volume-high' \
    'Terminal=false' \
    'Categories=Utility;AudioVideo;'
} > "$PREFIX/share/applications/notify-sound.desktop"

if [ "$AUTOSTART" = "1" ] && \
   [ -f "$CONFIG_HOME/notify-sound/config.json" ] && \
   grep -q '"autostart": *false' "$CONFIG_HOME/notify-sound/config.json"; then
  printf '%s\n' "Autostart omitido (deshabilitado en la configuración)"
  AUTOSTART=0
fi

if [ "$AUTOSTART" = "1" ]; then
  mkdir -p -m 700 "$CONFIG_HOME/autostart"
  {
    printf '%s\n' \
      '[Desktop Entry]' \
      'Type=Application' \
      'Name=NotifySound daemon' \
      'Comment=Play sounds for notifications that do not include one'
    printf 'Exec=%s --daemon\n' "$(desktop_exec "$PREFIX/bin/notify-sound")"
    printf '%s\n' \
      'Terminal=false' \
      'NoDisplay=true' \
      'X-GNOME-Autostart-enabled=true' \
      'X-GNOME-Autostart-Delay=2'
  } > "$CONFIG_HOME/autostart/notify-sound.desktop"
  chmod 600 "$CONFIG_HOME/autostart/notify-sound.desktop"
  printf 'Autostart habilitado (%s)\n' "$CONFIG_HOME/autostart/notify-sound.desktop"
else
  rm -f "$CONFIG_HOME/autostart/notify-sound.desktop"
fi

printf 'Instalado en %s\n' "$PREFIX"
printf '  Ejecutable: %s\n' "$PREFIX/bin/notify-sound"
printf '  Lanzador:   %s\n' "$PREFIX/share/applications/notify-sound.desktop"
printf '  Servicio:   %s\n' "$DEST/notify-sound.service"
printf '%s\n' 'Inicia la GUI con: notify-sound'

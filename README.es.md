# NotifySound

Reproduce sonidos para las notificaciones de escritorio que no incluyen uno.

En GNOME (y otros entornos de escritorio construidos sobre
`org.freedesktop.Notifications`), una notificación solo emite sonido cuando la
app que la envía incluye explícitamente un hint `sound-name` o `sound-file`.
La mayoría de las apps nunca lo hacen — Warp, terminales, builds de IDEs,
herramientas tipo cron — por lo que sus notificaciones permanecen en silencio.

NotifySound es un daemon pequeño que observa el bus de notificaciones y
reproduce un sonido de tu elección cada vez que llega una notificación sin
sonido propio. También incluye una app GTK4 sencilla para configurarlo todo.

**Instalación** (por usuario, activa el autostart):

```sh
git clone https://github.com/ChristianM023/notify-sound.git && cd notify-sound && ./install.sh
```

¿Ubuntu/Debian? [Instala el paquete de release](#paquete-debian) sin clonar
el repositorio.

## Requisitos

- Python 3.10+
- PyGObject (`python3-gi`)
- `canberra-gtk-play` (`gnome-session-canberra`)
- GTK 4.10+ (solo para la GUI)
- `dbus-monitor` (parte del paquete `dbus`, presente en todos los escritorios)

Instalación en Ubuntu/Debian:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gnome-session-canberra dbus
```

## Instalación

```sh
git clone https://github.com/ChristianM023/notify-sound.git
cd notify-sound
./install.sh
```

Esto instala en `~/.local/bin`, añade un lanzador y activa el autostart
(el daemon arranca con tu sesión). Usa `--no-autostart` para omitirlo.
Un `PREFIX=/some/path ./install.sh` personalizado instala en otra ubicación;
el wrapper exporta `NOTIFY_SOUND_BIN` para que el autostart y la GUI usen
siempre el binario correcto.

### Paquete Debian

Los usuarios de Ubuntu/Debian pueden instalar el paquete de release sin clonar
el repositorio:

```sh
sudo dpkg -i ./notify-sound_0.1.10_all.deb
sudo apt-get -f install
notify-sound
```

El paquete instala el programa en `/usr`, no activa un daemon automáticamente
e incluye una plantilla de unidad systemd a nivel de usuario:

```sh
mkdir -p ~/.config/systemd/user
cp /usr/share/notify-sound/notify-sound.service ~/.config/systemd/user/
systemctl --user enable --now notify-sound
```

### systemd (alternativa al autostart)

```sh
mkdir -p ~/.config/systemd/user
cp ~/.local/share/notify-sound/notify-sound.service ~/.config/systemd/user/
systemctl --user enable --now notify-sound
```

Para un `PREFIX` personalizado, copia la unidad generada desde
`$PREFIX/share/notify-sound/notify-sound.service` en su lugar.

### Desinstalación

```sh
rm -rf ~/.local/share/notify-sound ~/.local/bin/notify-sound
rm ~/.local/share/applications/notify-sound.desktop
rm ~/.config/autostart/notify-sound.desktop
rm -rf ~/.config/notify-sound
```

## Prueba rápida

Tras la instalación, comprueba que funciona en segundos:

```sh
notify-sound --daemon
notify-send "Test" "If you hear a sound, it works"
```

Si instalaste con el autostart activado (el valor por defecto), el daemon ya
está en ejecución y la línea `notify-send` por sí sola es suficiente.

## Cómo funciona

NotifySound ejecuta un daemon pequeño que escucha el bus de notificaciones
del escritorio. Cuando llega una notificación sin sonido propio, reproduce el
sonido que configuraste en la GUI.

Las notificaciones que ya llevan su propio sonido no se tocan, y las
notificaciones que declaran `suppress-sound` — apps que gestionan su propio
audio, como los navegadores basados en Chromium para contenido multimedia —
permanecen en silencio a propósito. Las apps que envían notificaciones se
recopilan automáticamente para que puedas ajustar cada una individualmente
(activar/desactivar, sonido por app).

Los detalles técnicos (contrato del parser, ciclo de vida del daemon) están
en la sección [Desarrollo](#desarrollo).

## Compatibilidad

| Nivel | Entornos |
|---|---|
| Compatible y probado | GNOME (validado en Ubuntu/Debian) |
| Compatible en teoría, aún sin probar | Otros escritorios freedesktop: KDE, XFCE, Cinnamon, MATE |
| No compatible | No Linux (Windows, macOS) |

NotifySound sigue la especificación de notificaciones freedesktop, por lo que
otros escritorios construidos sobre `org.freedesktop.Notifications` deberían
funcionar, pero aún no han sido validados — nada se afirma hasta que se prueba.

## Uso

```sh
notify-sound            # open the settings GUI
notify-sound --daemon   # start the daemon
notify-sound --quit     # stop the daemon
```

### Funciones de la GUI

- Interruptor maestro de encendido/apagado
- **Alternador de autostart**: arranca el daemon con tu sesión (escribe/elimina
  `~/.config/autostart/notify-sound.desktop`)
- Selector de sonido global (los sonidos del tema de sonido activo) + botón **Probar**
- Añade cualquier número de **archivos de sonido personalizados**; aparecen en
  los selectores de sonido global y por app (la lista está limitada a 3 filas
  visibles con scroll)
- Modo "No duplicar": conserva el sonido propio de la app cuando lo envía
- Activación/desactivación por app, selección de sonido por app y botón
  **Probar** por app
- **Alias de apps**: renombra las apps detectadas a un nombre visible amigable
  ("AIMP" en lugar del id del proceso en minúsculas) mediante el botón
  "Renombrar"
- **Fusión de apps**: renombrar una app a un alias ya en uso abre un popover
  de confirmación para fusionar las dos entradas; el duplicado se convierte en
  sinónimo y sus futuras notificaciones se atribuyen al superviviente
- **Popover de información de app**: el botón "Información" por app muestra el
  nombre de notificación sin procesar, el alias visible, el proceso detectado,
  el número de sinónimos, el número de notificaciones y la última vez vista;
  los sinónimos pueden restaurarse a entradas separadas desde aquí
- **Gestión de la lista de apps**: elimina apps individuales o usa
  "Vaciar lista" para restablecer todas las apps detectadas y su configuración
- **Ordenación de apps**: por llegada (por defecto), por nombre o por número
  de notificaciones
- Botones de arranque/parada del daemon con estado en vivo (la GUI es de
  instancia única: lanzarla de nuevo enfoca la ventana existente)

## Formatos de sonido personalizados

- **OGG, WAV, FLAC:** se reproducen con `canberra-gtk-play` (sin software
  adicional).
- **MP3, M4A, AAC, etc.:** `canberra-gtk-play` no puede decodificarlos, así
  que NotifySound recurre a `gst-launch-1.0` (GStreamer), luego a `ffplay`,
  `mpv` o `mpg123`. Instala al menos uno de ellos, p. ej.:

  ```sh
  sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  ```

## Configuración

Se almacena en `~/.config/notify-sound/config.json`:

```json
{
  "enabled": true,
  "sound": "message",
  "custom_sounds": ["/path/to/my-sound.mp3"],
  "no_duplicate": true,
  "autostart": true,
  "apps": {
    "warp": { "enabled": true, "sound": null },
    "aimp": {
      "enabled": true,
      "sound": null,
      "name": "AIMP",
      "synonyms": ["Canción A", "Canción B"]
    }
  }
}
```

`"sound"` es la elección actual: un id de sonido del tema (`message`,
`dialog-warning`, ...) o la ruta de uno de los archivos de `custom_sounds`.
`"sound": null` dentro de una app significa "heredar el sonido global". El
campo opcional `"name"` es un alias visible para la lista de apps
auto-detectadas (por ejemplo, para mostrar "AIMP" en lugar del id
`desktop-entry` en minúsculas); es editable desde el botón "Renombrar" de la
GUI y permanece limitado por `MAX_APP_NAME_LENGTH`. La lista opcional
`"synonyms"` (limitada por `MAX_SYNONYMS = 64`) registra valores `app_name`
sin procesar que deben atribuirse a esta entrada — se rellena al fusionar dos
entradas desde la GUI (renombrando una al alias de la otra) y el daemon la usa
para canonicalizar futuras notificaciones de esas fuentes.

`~/.config/notify-sound/state.json` contiene la lista de apps auto-detectadas
y los metadatos por app:

```json
{
  "apps_seen": ["aimp", "warp", "Telegram Desktop"],
  "app_meta": {
    "aimp": { "seen_count": 12, "last_seen": 1777000000.5, "comm": "aimp" }
  }
}
```

La lista de apps es auto-detectada, por lo que las entradas se vuelven a
añadir cuando la app envía una notificación nueva. `app_meta` alimenta el
popover "Información" (recuento, última vez vista, proceso detectado) y lo
escribe el daemon; es opcional y se tolera su ausencia (los archivos de estado
de v0.1.7 siguen cargando).

## Privacidad

NotifySound procesa todo localmente: nunca se conecta a la red, no envía
telemetría y no almacena contenido de notificaciones. El daemon lee solo
metadatos (nombre de la app, hints) del bus de notificaciones y los mantiene
en memoria. Los únicos datos por app escritos en disco son `app_meta` en
`~/.config/notify-sound/state.json` (número de notificaciones, última vez
vista, proceso detectado) — configuración del usuario, nunca el cuerpo de una
notificación.

Para observar las notificaciones, el daemon ejecuta `dbus-monitor` en modo
eavesdrop, la única forma fiable de vigilar el bus de notificaciones desde
Python. Los archivos de configuración y estado se escriben atómicamente
(archivo temporal + rename) con `O_NOFOLLOW` y modo 0600, de modo que solo tu
usuario puede leerlos.

## Solución de problemas

- **No suena nada:** asegúrate de que `canberra-gtk-play` está instalado y
  pruébalo con `canberra-gtk-play -i message`. Verifica que el daemon está en
  ejecución con `notify-sound --quit` (informa "not running") o consulta el
  estado en la GUI.
- **Un archivo de sonido personalizado (MP3, M4A...) no suena:**
  `canberra-gtk-play` solo decodifica OGG/WAV/FLAC. Para otros formatos
  NotifySound recurre a `gst-launch-1.0` (GStreamer), luego a `ffplay`, `mpv`
  o `mpg123` — instala al menos uno de ellos, p. ej. `sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good`.
- **Las notificaciones con sonido propio se reproducen dos veces:** activa
  "no_duplicate" en la GUI (desactivado significa que ambos sonidos suenan a
  propósito). Si una app envía `suppress-sound`, NotifySound se silencia por
  diseño.
- **Llega una notificación pero no reproduce sonido:** si la app envía
  `suppress-sound`, es intencional (la app reproduce su propio sonido). Si no,
  comprueba el alternador de esa app en la GUI.
- **El sonido suena solo después de la siguiente notificación:** actualiza a
  0.1.1 o posterior; NotifySound procesa cada mensaje D-Bus inmediatamente.
  Si el reproductor arranca de inmediato pero el audio llega con retraso,
  repórtalo con detalles de tu shell y tu servidor de notificaciones.

## Desarrollo

### Estructura

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

### Ejecutar los tests

```sh
python3 -m unittest discover -s tests -v
```

La suite usa los helpers `notification()` y `gtk_notification()` para
construir salida realista de dbus-monitor (incluyendo cuerpos multilínea y
hints) y verifica qué se reproduciría/omitiría. Añade un test para cada
comportamiento nuevo.

### Contrato del parser (leer antes de tocar `daemon.py`)

- El hilo lector consume el stdout de dbus-monitor línea a línea. Un mensaje
  `Notify` estándar empieza en una línea que coincide con `_MESSAGE_HEADER_RE`
  y **termina en su línea top-level `int32 <n>`** (`_TOP_LEVEL_INT32_RE`:
  exactamente 3 espacios + `int32`). Es el argumento de timeout de Notify —
  siempre el último argumento, y el único `int32` top-level en un mensaje
  Notify.
- Las aplicaciones GTK como Ptyxis usan
  `org.gtk.Notifications.AddNotification`. Esos mensajes terminan en el
  corchete de cierre de su `array` top-level exterior, fuera de las cadenas
  entre comillas. Los mensajes incompletos o sobredimensionados se descartan.
- **No** reintroduzcas la separación de mensajes basada en líneas en blanco o
  columna 0: los valores de cadena con saltos de línea incrustados (`"a\n\nb"`)
  se imprimen como líneas físicamente en blanco y sin sangría dentro de un
  mensaje. Esta fue la causa raíz de la doble reproducción en apps de chat
  (v0.1.4).
- Los marcadores de framing y los encabezados de mensaje solo se reconocen
  fuera de las cadenas entre comillas, de modo que el contenido de una
  notificación no puede imitar un terminador o un encabezado.
- gnome-shell reenvía cada notificación con los hints `x-shell-sender` +
  `x-shell-sender-pid`; esos bloques deben omitirse.
- El nombre canónico de la app (usado para la búsqueda de configuración por
  app y para la lista de apps auto-detectadas) se resuelve, en orden, desde:
  1. el **hint `desktop-entry`** cuando está presente y es válido; si no,
  2. el **`comm` del proceso** resuelto desde la conexión `sender` de D-Bus
     mediante `dbus-send … GetConnectionUnixProcessID` + `/proc/<pid>/comm`
     (cache de 5 min; omite intérpretes genéricos como `python3`/`sh`, con
     fallback a `/proc/<pid>/cmdline`); si no,
  3. el **primer sinónimo coincidente** declarado en la lista `synonyms` de
     otra app en `config.json`; si no,
  4. el primer argumento `string` sin procesar (`app_name`).
  Esto corrige apps como AIMP que envían el título de la canción como
  `app_name` y de otro modo crearían una entrada de lista por canción. El
  valor del hint `desktop-entry` se lee de la línea `variant string "..."`
  que sigue a la clave `string "desktop-entry"`; las cadenas entre comillas
  siguen siendo rastreadas por la misma máquina de estados, de modo que ni la
  clave ni el valor pueden falsear el framing.
- Reglas de reproducción, en orden: `enabled` maestro apagado → nada; hint
  `suppress-sound` → siempre nada; app desactivada → nada; hint
  `sound-file`/`sound-name` con `no_duplicate` → nada; si no, reproduce la
  elección de la app o la elección global (un id de tema o una ruta de archivo
  personalizada).

### Ciclo de vida del daemon

- Instancia única: `config.acquire_instance_lock()` toma un `flock` sobre el
  archivo de lock permanente (`$XDG_RUNTIME_DIR/notify-sound.pid.lock`, con
  fallback por usuario en `~/.cache/notify-sound/`), abierto con `O_NOFOLLOW`
  y modo 0600. El PID se escribe atómicamente en el archivo `.pid` adyacente.
- `dbus-monitor` se reinicia con backoff exponencial (1s → 30s) si sale; el
  backoff se resetea tras 5s de estabilidad. Si el binario falta, el daemon
  reintenta en segundo plano en lugar de morir. Observa tanto
  `org.freedesktop.Notifications.Notify` como
  `org.gtk.Notifications.AddNotification`.
- `SIGTERM`/`SIGINT` detienen el bucle principal, terminan el monitor y
  eliminan el pidfile. `--quit` envía `SIGTERM` al pid del pidfile.

### Checklist de release

1. Sube `__version__` en `notify_sound/__init__.py`.
2. Añade una entrada de Changelog abajo y actualiza los enlaces de release
   versionados.
3. Ejecuta la suite completa de tests.
4. `./install.sh` para desplegar, luego reinicia el daemon
   (`notify-sound --quit && notify-sound --daemon`).
5. Fusiona mediante un PR con el check `tests` requerido.
6. Construye el paquete Debian y su checksum:

   ```sh
   ./build-deb.sh
   sha256sum dist/notify-sound_X.Y.Z_all.deb \
     > dist/notify-sound_X.Y.Z_all.deb.sha256
   ```

7. Crea y sube el tag `vX.Y.Z` correspondiente, crea un draft de GitHub
   release, sube el `.deb` y el checksum, y publica la release.
8. Verifica el checksum de la release y el despliegue correcto de GitHub
   Pages.

## Changelog

- **0.1.10** — corrección de los contadores e instantes del info de app que no
  se actualizaban tras la primera notificación (el daemon ahora persiste
  `seen_count`/`last_seen` en cada `_record_app`); corrección de los nombres de
  app largos que se cortaban, ampliando la ventana y dando a la etiqueta del
  nombre un mínimo de 28 caracteres, compactando el botón **Probar** por fila
  en un icono de reproducción con tooltip. Corrección de la truncación del
  nombre de proceso: fallback a `/proc/PID/cmdline` cuando `comm` está truncado
  por el kernel a 15 caracteres (`telegram-deskto` → `telegram-desktop`).
  Mejoras de GUI: la lista de sonidos personalizados está limitada a ~3 filas
  con scroll propio (ya no roba espacio vertical a la lista de apps); la lista
  de apps puede ordenarse por llegada (por defecto), por nombre o por número de
  notificaciones mediante un nuevo desplegable en el encabezado "Aplicaciones";
  la etiqueta de estado del daemon se movió junto a los botones de
  arranque/parada; botón de eliminar por app y "Vaciar lista" para restablecer
  toda la lista detectada.
- **0.1.9** — auto-consolidación de apps como AIMP que envían el título de la
  canción como `app_name` y sin hint `desktop-entry`: el daemon ahora resuelve
  la conexión `sender` de D-Bus a un PID (`dbus-send
  org.freedesktop.DBus.GetConnectionUnixProcessID`) y usa el nombre
  `comm`/`cmdline` del proceso como id canónico estable (cache de 5 min,
  tolerante cuando `dbus-send` o `/proc` no están disponibles, y omitiendo
  intérpretes genéricos como `python3`/`sh`). La cadena canónica ahora es:
  `desktop-entry` → `comm` resuelto → sinónimo conocido → `app_name` sin
  procesar. Fusión manual: renombrar una app a un alias ya en uso abre un
  diálogo para fusionar las dos entradas (el duplicado se convierte en
  sinónimo, con `enabled` combinado con AND y `sound` heredado); los alias
  duplicados que quedan en `config.json` también se fusionan automáticamente
  al cargar. El nuevo **popover "Información"** (por app) muestra el nombre de
  notificación sin procesar, el alias visible, el proceso detectado, los
  sinónimos, el número de notificaciones y la última vez vista. `state.json`
  ahora almacena `app_meta` (count / `last_seen` / `comm`) de forma
  retrocompatible. `MAX_SYNONYMS = 64` limita las listas de sinónimos.
- **0.1.8** — corrección de AIMP y otras apps que envían el título de la
  canción como `app_name` de la notificación: cuando el hint `desktop-entry`
  está presente, se usa como nombre canónico de la app (de modo que la GUI
  muestra una fila estable "aimp" en lugar de una entrada por canción); alias
  manual de apps mediante el nuevo botón "Renombrar" (Popover con `Gtk.Entry`)
  para los casos en que el hint no se envía; correcciones de GUI: los nombres
  de app largos ahora se recortan con puntos suspensivos y tooltip en lugar de
  empujar el desplegable/botón/interruptor fuera de la vista, y cada
  interruptor está centrado verticalmente (`valign=CENTER`) para que ya no se
  estire con la altura de la fila. Tests de regresión del parser añadidos para
  `desktop-entry`, `x-shell-sender` + `desktop-entry`, `sound-name` +
  `desktop-entry` y la búsqueda de configuración por app mediante el nombre
  canónico.
- **0.1.7** — mejoras del paquete Debian y su checksum, sonidos de tema FLAC y
  un checklist de release completo.
- **0.1.6** — endurecimiento de seguridad: parsing de notificaciones acotado y
  consciente de comillas, soporte de notificaciones GTK (incluida Ptyxis),
  escrituras atómicas privadas de config/state, timeouts de reproducción y
  decodificador acotados, validación segura de sonido/rutas, instalador/
  autostart endurecidos y acciones de Pages fijadas.
- **0.1.5** — corrección del cierre del daemon: orden terminate/wait/kill para
  que `--quit` detenga siempre el daemon y su `dbus-monitor` limpiamente.
- **0.1.4** — corrección del parsing multilínea: el final del mensaje se
  detecta mediante la línea top-level `int32` de timeout en lugar de líneas en
  blanco (los cuerpos multilínea causaban doble reproducción y ocultaban
  hints). Respetar siempre el hint `suppress-sound` (las apps que gestionan su
  propio sonido permanecen en silencio — no más doble sonido con PWAs de
  Chromium como WhatsApp Web).
- **0.1.3** — la GUI suelta su retención al cerrar la ventana, de modo que el
  proceso sale en lugar de quedarse residente.
- **0.1.2** — la GUI es de instancia única: relanzarla enfoca la ventana
  existente en lugar de abrir una nueva.
- **0.1.1** — endurecimiento del daemon y la config: parsing inmediato (sin
  esperar a la siguiente notificación), reproducción segura cuando falta
  `canberra-gtk-play`, backoff exponencial de reinicio, lock de instancia
  atómico (sin pidfiles obsoletos, sin colisión en `/tmp`), parsing tolerante
  de config, instalación/autostart conscientes de PREFIX, fallback de
  reproducción para MP3/otros formatos, suite de regresión.
- **0.1.0** — release inicial: daemon, GUI GTK4, sonidos de tema/
  personalizados, config por app, autostart, script de instalación.

## ¿Por qué existe esto?

Contexto: GNOME reproduce sonidos de notificación solo cuando la notificación
lleva un hint de sonido (según la especificación de notificaciones
freedesktop). Discusiones relevantes:

- [El mantenedor de gnome-shell explica el comportamiento](https://discourse.gnome.org/t/no-notification-sound/19933)
- Warp: [play_notification_sound es solo de macOS](https://github.com/warpdotdev/warp/issues/8901) /
  [solicitud de función: sonido tras comandos largos](https://github.com/warpdotdev/warp/issues/4155)

## Licencia

MIT

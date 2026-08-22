# Demo audiovisual — instrucciones para el operador

Este directorio contendrá el material audiovisual del ticket VIS-005.

## Qué grabar

Un GIF o video corto (< 30s) que muestre:
1. Una notificación silenciosa llegando (sin sonido propio).
2. NotifySound reproduciendo el sonido configurado.

## Cómo grabar

1. Asegúrate de que el daemon está corriendo: `notify-sound --daemon`
2. Abre la GUI: `notify-sound`
3. Verifica que hay un sonido seleccionado (p. ej. "message" del tema)
4. Inicia la grabación de pantalla (p. ej. con `kooha`, `OBS Studio`, o `gnome-screen-recorder`)
5. Envía una notificación de prueba: `notify-send "Test" "If you hear a sound, it works"`
6. Detén la grabación
7. Si es video, conviértelo a GIF si prefieres ese formato (p. ej. con `ffmpeg`)

## Formato y nombre

- GIF: `demo.gif` (recomendado para la web, se reproduce automáticamente)
- O video MP4: `demo.mp4` (con `<video autoplay loop muted>`)
- Resolución: 1280x720 o similar
- Duración: < 30s

## Después de grabar

1. Coloca el archivo en este directorio (`site/demo/`)
2. Edita `site/index.html` y `site/es/index.html`: reemplaza el `div.demo-placeholder` en la sección "Demo" con el `<img>` o `<video>` correspondiente
3. Elimina el comentario `<!-- TODO: ... -->`
4. Marca el criterio "Demo audiovisual" como completado en `spec/constitution/backlog.md` (VIS-005)

# Demo audiovisual — NotifySound

Registro del material audiovisual del ticket VIS-005, integrado en la sección "Demo" de la landing (`site/index.html` y `site/es/index.html`).

## Qué es

`demo.mp4` es el video de demostración de NotifySound. Se reproduce en la sección "Demo" de la landing con `<video controls autoplay loop muted playsinline>`.

## Qué muestra

1. Una notificación silenciosa llegando (sin sonido propio).
2. NotifySound reproduciendo el sonido configurado.

El caption de la landing lo resume: primero sin NotifySound (silenciosa), luego con NotifySound (suena).

## Cómo se grabó

- OBS Studio, grabación de escritorio completo.
- Medidor de volumen visible para evidenciar el sonido.
- Daemon corriendo (`notify-sound --daemon`) con un sonido seleccionado (p. ej. "message" del tema).
- Notificación de prueba enviada con `notify-send "Test" "If you hear a sound, it works"`.

## Formato

- Archivo: `demo.mp4`
- Formato: MP4
- Duración: 17s
- Tamaño: 2.7MB

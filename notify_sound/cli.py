"""Lógica del subcomando CLI `done` del entrypoint `notify-sound`.

Extraída del script suelto `notify-sound` (sin extensión .py) para que la
lógica sea testeable con regresiones (unittest). El entrypoint solo parsea
argumentos y delega aquí; esta función retorna el exit code.
"""

import sys

from notify_sound import config, notify, player


def run_done(message=None):
    """Ejecuta el subcomando `done` y retorna el exit code.

    Envía la notificación de finalización vía `notify.send_done_notification`
    (el helper normaliza mensaje vacío o None a "Comando finalizado"). Si el
    envío falla, imprime el error en stderr y retorna 1. Si el envío es
    exitoso y el daemon no está corriendo, reproduce `done_sound`
    directamente como fallback; si el daemon corre, no reproduce nada (el
    daemon se encarga, mutuamente excluyente para evitar duplicados).
    """
    ok, error = notify.send_done_notification(message)
    if not ok:
        print(error, file=sys.stderr)
        return 1
    if not config.is_running():
        cfg = config.load_config()
        done_sound = cfg.get("done_sound") or "complete"
        player.play_choice(done_sound)
    return 0
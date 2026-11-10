"""
main.py — Orquesta el día completo: fetch → render → (push) → carrusel → (push) → reel → (push).

Instagram descarga las fotos de raw.githubusercontent.com, así que tienen que estar
en GitHub ANTES de publicar. Con --push, main.py sube data/ y out/, espera a que las
fotos se vean en raw, publica y sube state/. Sin --push no toca git: es lo normal en
local, y publicar solo funciona si las fotos de ese día ya están subidas.

El reel va después del carrusel y por separado: se genera en local con HyperFrames,
se sube directamente a Meta (no pasa por el repo) y tiene su propio estado. Si el reel
falla, el carrusel ya está publicado y el siguiente disparo reintenta solo el reel.

Uso en local (desde la raíz del repo, con el entorno pvpc activo):
    python main.py --sin-publicar                     # D+1: datos, fotos y reel, nada más
    python main.py --fecha 2026-11-04 --sin-publicar
    python main.py --fecha 2026-11-04 --prueba        # todo menos publicar
    python main.py --fecha 2026-11-04 --prueba --sin-reel
    python main.py --push                             # flujo real (GitHub Actions)

Códigos de salida: 0 = ok o ya publicado, 2 = pendiente (no es un error), 1 = error.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone

from src import state
from src.fetch_pvpc import ROOT, TZ, cargar_env, manana_madrid, obtener_pvpc
from src.publish import PublishError, publicar_carrusel, publicar_reel, urls_publicas
from src.render import render

SALIDA_OK, SALIDA_ERROR, SALIDA_PENDIENTE = 0, 1, 2

# GitHub retrasa a veces los disparos programados varias horas (el 9 y el 10/11/2026 salieron
# pasada la medianoche de Madrid). Hasta esta hora, un disparo sin --fecha remata el día de HOY
# si quedó sin publicar, en vez de saltar a D+1 (que aún no tiene datos) y perder el día.
HORA_LIMITE_RESCATE = 12

# raw.githubusercontent.com tiene caché: un archivo recién subido puede tardar en verse.
ESPERA_RAW_MAX_S = 360
ESPERA_RAW_PASO_S = 20

log = logging.getLogger("main")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)


def _rama() -> str:
    return os.getenv("GITHUB_BRANCH", "main")


def sincronizar() -> None:
    """Trae lo último de GitHub antes de empezar.

    Un «Re-run» de Actions vuelve a usar el commit de la primera ejecución: sin esto trabajaría
    con un state/published.json viejo (p. ej. sin el carrusel que ya publicó el primer intento).
    """
    r = _git("pull", "--rebase", "--autostash", "origin", _rama())
    if r.returncode != 0:
        raise RuntimeError(f"git pull falló: {r.stderr.strip()}")
    log.info("Repo al día con origin/%s", _rama())


def subir(rutas: list[str], mensaje: str) -> bool:
    """Commit y push de esas rutas. Devuelve False si no había nada que subir.

    Si el push se rechaza porque alguien subió algo mientras tanto, se rebasa y se reintenta una vez.
    """
    r = _git("add", "--", *rutas)
    if r.returncode != 0:
        raise RuntimeError(f"git add falló: {r.stderr.strip()}")
    if _git("diff", "--cached", "--quiet").returncode == 0:
        log.info("Sin cambios que subir en %s", ", ".join(rutas))
        return False
    r = _git("commit", "-m", mensaje)
    if r.returncode != 0:
        raise RuntimeError(f"git commit falló: {r.stderr.strip()}")
    r = _git("push", "origin", f"HEAD:{_rama()}")
    if r.returncode != 0:
        log.info("Push rechazado (%s): se rebasa sobre origin/%s y se reintenta",
                 r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "?", _rama())
        rb = _git("pull", "--rebase", "origin", _rama())
        if rb.returncode != 0:
            _git("rebase", "--abort")
            raise RuntimeError(f"git pull --rebase falló: {rb.stderr.strip()}")
        r = _git("push", "origin", f"HEAD:{_rama()}")
        if r.returncode != 0:
            raise RuntimeError(f"git push falló: {r.stderr.strip()}")
    log.info("Subido: %s", mensaje)
    return True


def esperar_fotos(fecha: date) -> None:
    """Espera a que raw.githubusercontent.com sirva las fotos actuales del día."""
    repo = os.getenv("GITHUB_REPOSITORY", "angelmoslares/pvpc-instagram")
    rama = _rama()
    t0 = time.monotonic()
    while True:
        try:
            urls_publicas(fecha, repo, rama)
            return
        except PublishError as e:
            if time.monotonic() - t0 > ESPERA_RAW_MAX_S:
                raise
            log.info("Fotos aún no visibles en raw: %s", e)
            time.sleep(ESPERA_RAW_PASO_S)


def fase_carrusel(fecha: date, fuente: str, sin_publicar: bool, prueba: bool, forzar: bool, push: bool) -> int:
    res = obtener_pvpc(fecha, fuente)
    if not res.ok:
        log.info("PENDIENTE %s: %s", fecha, res.motivo)
        return SALIDA_PENDIENTE
    log.info("Datos OK: %s, %d horas, fuente %s", fecha, len(res.horario), res.fuente)

    for ruta in render(fecha):
        log.info("Foto OK: %s", ruta.relative_to(ROOT))

    if sin_publicar:
        return SALIDA_OK

    if push:
        subir(["data", "out"], f"Datos y fotos del {fecha}")
        esperar_fotos(fecha)

    publicar_carrusel(fecha, prueba=prueba, forzar=forzar)

    if push and not prueba:
        subir(["state"], f"Publicado el {fecha}")
    return SALIDA_OK


def fase_reel(fecha: date, sin_publicar: bool, prueba: bool, forzar: bool, push: bool) -> int:
    """Genera el reel (con comprobación de fidelidad) y lo publica. Nunca toca el carrusel."""
    from src.render_reel import render_reel  # importa Node/HyperFrames solo si hace falta

    mp4 = render_reel(fecha, comprobar=True)
    log.info("Reel OK: %s", mp4.relative_to(ROOT))
    if sin_publicar:
        return SALIDA_OK
    try:
        publicar_reel(fecha, prueba=prueba, forzar=forzar)
    finally:
        # Aunque falle la publicación, se sube lo que haya del estado (p. ej. el contenedor ya
        # subido), para que el siguiente disparo lo reutilice en vez de volver a subir el vídeo.
        if push and not prueba:
            subir(["state"], f"Reel del {fecha}")
    return SALIDA_OK


def fecha_objetivo(ahora: datetime | None = None, con_reel: bool = True) -> date:
    """Fecha que toca publicar cuando no se pasa --fecha.

    Normalmente D+1 (hora de Madrid). Pero si el disparo llega de madrugada (antes de las
    HORA_LIMITE_RESCATE) y lo de hoy no está completo (carrusel, o reel si se pide), devuelve hoy.
    Lee state/published.json, así que con --push hay que llamarla después de sincronizar().
    """
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(TZ)
    hoy = ahora.date()
    if ahora.hour < HORA_LIMITE_RESCATE:
        falta_carrusel = not state.ya_publicado(hoy)
        falta_reel = con_reel and not state.ya_publicado(hoy, "reel")
        if falta_carrusel or falta_reel:
            log.info("Disparo de madrugada (%s Madrid) y el %s no está completo: se publica hoy.",
                     ahora.strftime("%H:%M"), hoy)
            return hoy
    return manana_madrid(ahora)


def ejecutar(fecha: date, fuente: str = "auto", sin_publicar: bool = False, prueba: bool = False,
             forzar: bool = False, push: bool = False, con_reel: bool = True) -> int:
    if push:
        sincronizar()  # el estado tiene que ser el de GitHub, no el del commit con el que arrancó el job
    hecho_carrusel = bool(state.ya_publicado(fecha)) and not forzar
    hecho_reel = (not con_reel) or (bool(state.ya_publicado(fecha, "reel")) and not forzar)

    if hecho_carrusel and hecho_reel:
        log.info("El %s ya está publicado. No se repite.", fecha)
        return SALIDA_OK

    if hecho_carrusel:
        log.info("Carrusel del %s ya publicado (%s): solo falta el reel.",
                 fecha, state.ya_publicado(fecha).get("permalink"))
    else:
        codigo = fase_carrusel(fecha, fuente, sin_publicar, prueba, forzar, push)
        if codigo != SALIDA_OK:
            return codigo

    if hecho_reel:
        return SALIDA_OK
    try:
        return fase_reel(fecha, sin_publicar, prueba, forzar, push)
    except Exception as e:  # noqa: BLE001 — el carrusel ya está hecho: se informa y sale con error
        log.error("ERROR en el reel (%s): %s. El carrusel no se ve afectado; "
                  "el siguiente disparo lo reintentará.", type(e).__name__, e)
        return SALIDA_ERROR


def _main() -> int:
    ap = argparse.ArgumentParser(description="Genera y publica el PVPC de un día (carrusel y reel).")
    ap.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid; de madrugada, hoy si quedó sin publicar)")
    ap.add_argument("--fuente", choices=["auto", "esios", "redata"], default="auto")
    ap.add_argument("--sin-publicar", action="store_true", help="Solo datos, fotos y reel")
    ap.add_argument("--prueba", action="store_true", help="Hace todo menos publicar")
    ap.add_argument("--forzar", action="store_true", help="Repite aunque el día figure como publicado")
    ap.add_argument("--push", action="store_true", help="Sube a GitHub datos, fotos y estado")
    ap.add_argument("--sin-reel", action="store_true", help="Solo el carrusel")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cargar_env()
    try:
        if a.fecha:
            fecha = a.fecha
        else:
            if a.push:
                sincronizar()  # la fecha depende del estado: tiene que ser el de GitHub
            fecha = fecha_objetivo(con_reel=not a.sin_reel)
        log.info("Fecha objetivo: %s", fecha)
        return ejecutar(fecha, a.fuente, a.sin_publicar, a.prueba, a.forzar, a.push, not a.sin_reel)
    except Exception as e:  # noqa: BLE001 — último nivel: cualquier fallo acaba en log y salida 1
        log.error("ERROR (%s): %s", type(e).__name__, e)
        return SALIDA_ERROR


if __name__ == "__main__":
    sys.exit(_main())

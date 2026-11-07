"""
main.py — Orquesta el día completo: fetch → render → (push) → publish → (push).

Instagram descarga las fotos de raw.githubusercontent.com, así que tienen que estar
en GitHub ANTES de publicar. Con --push, main.py sube data/ y out/, espera a que las
fotos se vean en raw, publica y sube state/. Sin --push no toca git: es lo normal en
local, y publicar solo funciona si las fotos de ese día ya están subidas.

Uso en local (desde la raíz del repo, con el entorno pvpc activo):
    python main.py --sin-publicar                     # D+1: datos y fotos, nada más
    python main.py --fecha 2026-11-04 --sin-publicar
    python main.py --fecha 2026-11-04 --prueba        # todo menos publicar
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
from datetime import date

from src import state
from src.fetch_pvpc import ROOT, cargar_env, manana_madrid, obtener_pvpc
from src.publish import PublishError, publicar_carrusel, urls_publicas
from src.render import render

SALIDA_OK, SALIDA_ERROR, SALIDA_PENDIENTE = 0, 1, 2

# raw.githubusercontent.com tiene caché: un archivo recién subido puede tardar en verse.
ESPERA_RAW_MAX_S = 360
ESPERA_RAW_PASO_S = 20

log = logging.getLogger("main")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)


def subir(rutas: list[str], mensaje: str) -> bool:
    """Commit y push de esas rutas. Devuelve False si no había nada que subir."""
    r = _git("add", "--", *rutas)
    if r.returncode != 0:
        raise RuntimeError(f"git add falló: {r.stderr.strip()}")
    if _git("diff", "--cached", "--quiet").returncode == 0:
        log.info("Sin cambios que subir en %s", ", ".join(rutas))
        return False
    for paso in (("commit", "-m", mensaje), ("push", "origin", "HEAD")):
        r = _git(*paso)
        if r.returncode != 0:
            raise RuntimeError(f"git {paso[0]} falló: {r.stderr.strip()}")
    log.info("Subido: %s", mensaje)
    return True


def esperar_fotos(fecha: date) -> None:
    """Espera a que raw.githubusercontent.com sirva las fotos actuales del día."""
    repo = os.getenv("GITHUB_REPOSITORY", "angelmoslares/pvpc-instagram")
    rama = os.getenv("GITHUB_BRANCH", "main")
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


def ejecutar(fecha: date, fuente: str = "auto", sin_publicar: bool = False, prueba: bool = False,
             forzar: bool = False, push: bool = False) -> int:
    previo = state.ya_publicado(fecha)
    if previo and not forzar:
        log.info("El %s ya está publicado (%s). No se repite.", fecha, previo.get("permalink"))
        return SALIDA_OK

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


def _main() -> int:
    ap = argparse.ArgumentParser(description="Genera y publica el PVPC de un día.")
    ap.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid)")
    ap.add_argument("--fuente", choices=["auto", "esios", "redata"], default="auto")
    ap.add_argument("--sin-publicar", action="store_true", help="Solo datos y fotos")
    ap.add_argument("--prueba", action="store_true", help="Hace todo menos publicar")
    ap.add_argument("--forzar", action="store_true", help="Repite aunque el día figure como publicado")
    ap.add_argument("--push", action="store_true", help="Sube a GitHub datos, fotos y estado")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cargar_env()
    fecha = a.fecha or manana_madrid()
    try:
        return ejecutar(fecha, a.fuente, a.sin_publicar, a.prueba, a.forzar, a.push)
    except Exception as e:  # noqa: BLE001 — último nivel: cualquier fallo acaba en log y salida 1
        log.error("ERROR (%s): %s", type(e).__name__, e)
        return SALIDA_ERROR


if __name__ == "__main__":
    sys.exit(_main())

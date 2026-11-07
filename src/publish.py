"""
publish.py — Publica el carrusel de un día en Instagram.

Admite los dos tipos de token de Meta y elige el host según el prefijo:
    - EAA…  (API de Instagram con inicio de sesión con Facebook: token de PÁGINA)
            → graph.facebook.com, IG_USER_ID = instagram_business_account de la página.
    - IG…   (API de Instagram con inicio de sesión de Instagram)
            → graph.instagram.com, IG_USER_ID = user_id de /me.
Se puede forzar con IG_GRAPH_HOST=graph.facebook.com | graph.instagram.com.

Flujo:
    1. Comprueba que el día no está ya en state/published.json.
    2. Comprueba que las 3 fotos están en GitHub y se descargan como JPEG desde
       raw.githubusercontent.com (Meta las descarga de ahí; si no hay push, falla).
    3. Crea un contenedor por foto (is_carousel_item=true) y espera a FINISHED.
    4. Crea el contenedor CAROUSEL con el pie de foto y espera a FINISHED.
    5. Publica (media_publish), guarda el estado y muestra el enlace.

Uso en local (desde la raíz del repo, con IG_TOKEN e IG_USER_ID en .env):
    python -m src.publish --fecha 2026-11-04 --prueba   # todo menos publicar
    python -m src.publish --fecha 2026-11-04            # publica de verdad
    python -m src.publish --pie --fecha 2026-11-04      # solo muestra el pie de foto

El token nunca se imprime: va en la cabecera Authorization.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date

import requests

from src import state
from src.fetch_pvpc import ROOT, cargar_env, manana_madrid
from src.render import OUT_DIR, contexto, verificar

TIMEOUT_S = 60
API_VERSION_DEFECTO = "v27.0"
DIAS_LARGOS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"]

log = logging.getLogger("publish")


class PublishError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Pie de foto (mismos números que las imágenes: salen del mismo contexto)
# --------------------------------------------------------------------------- #
def pie_de_foto(fecha: date) -> str:
    c = contexto(fecha)
    verificar(c)
    dia = f"{DIAS_LARGOS[fecha.weekday()]} {fecha.day} de {MESES_LARGOS[fecha.month - 1]}"
    lav, vaj = c["escenas"]["lavadora"], c["escenas"]["lavavajillas"]
    return "\n".join([
        f"💡 Precio de la luz del {dia}",
        "",
        f"Más barata: {c['min']['cifra']} €/kWh, {c['min']['rango']}",
        f"Media del día: {c['media']['cifra']} €/kWh",
        f"Más cara: {c['max']['cifra']} €/kWh, {c['max']['rango']}",
        "",
        f"Lavadora: {lav['mejor_coste']} € en la hora barata y {lav['peor_coste']} € en la cara.",
        f"Lavavajillas: {vaj['mejor_coste']} € en la hora barata y {vaj['peor_coste']} € en la cara.",
        "",
        "Desliza para verlo con calma. Que la luz te acompañe… y que sea barata.",
        "",
        "Término de energía del PVPC 2.0TD (Península), sin impuestos. Fuente: REE (ESIOS). "
        f"Consumo por ciclo: lavadora {lav['kwh']} kWh, lavavajillas {vaj['kwh']} kWh (medias OCU).",
        "",
        "#preciodelaluz #pvpc #tarifadelaluz #facturadelaluz #ahorroenergetico #luz",
    ])


# --------------------------------------------------------------------------- #
# Graph API
# --------------------------------------------------------------------------- #
def host_y_cabecera(token: str) -> tuple[str, dict]:
    """Host de la Graph API y cabecera Authorization según el tipo de token."""
    host = os.getenv("IG_GRAPH_HOST", "").strip()
    if not host:
        host = "graph.facebook.com" if token.startswith("EAA") else "graph.instagram.com"
    # graph.facebook.com: esquema OAuth (probado). graph.instagram.com: Bearer.
    esquema = "OAuth" if host == "graph.facebook.com" else "Bearer"
    return host, {"Authorization": f"{esquema} {token}"}


class Instagram:
    def __init__(self, token: str, user_id: str, version: str):
        if not token or not user_id:
            raise PublishError("Faltan IG_TOKEN o IG_USER_ID (en .env o en GitHub Secrets).")
        self.host, cabecera = host_y_cabecera(token)
        self.base = f"https://{self.host}/{version}"
        self.user_id = user_id
        self.s = requests.Session()
        self.s.headers.update(cabecera)

    def _req(self, metodo: str, ruta: str, **kw) -> dict:
        try:
            r = self.s.request(metodo, f"{self.base}/{ruta}", timeout=TIMEOUT_S, **kw)
        except requests.RequestException as e:
            raise PublishError(f"{self.host} no responde: {type(e).__name__}") from e
        try:
            datos = r.json()
        except ValueError:
            datos = {}
        if r.status_code != 200 or "error" in datos:
            err = datos.get("error", {})
            raise PublishError(f"HTTP {r.status_code} en {ruta.split('?')[0]}: "
                               f"{err.get('message', r.text[:200])} (code {err.get('code')}, "
                               f"subcode {err.get('error_subcode')})")
        return datos

    def contenedor(self, **datos) -> str:
        return self._req("POST", f"{self.user_id}/media", data=datos)["id"]

    def esperar(self, cid: str, max_s: int = 300) -> None:
        """Espera a FINISHED: rápido al principio y luego cada 30 s, como pide Meta, máximo 5 min."""
        t0 = time.monotonic()
        espera = 3
        while True:
            estado = self._req("GET", cid, params={"fields": "status_code,status"})
            codigo = estado.get("status_code")
            if codigo == "FINISHED":
                return
            if codigo in ("ERROR", "EXPIRED"):
                raise PublishError(f"Contenedor {cid} en estado {codigo}: {estado.get('status', '')}")
            if time.monotonic() - t0 > max_s:
                raise PublishError(f"Contenedor {cid} sigue en {codigo} tras {max_s} s")
            time.sleep(espera)
            if time.monotonic() - t0 > 30:
                espera = 30

    def publicar(self, creation_id: str) -> str:
        return self._req("POST", f"{self.user_id}/media_publish", data={"creation_id": creation_id})["id"]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._req("GET", media_id, params={"fields": "permalink"}).get("permalink")
        except PublishError:
            return None

    def cuota(self) -> str:
        try:
            d = self._req("GET", f"{self.user_id}/content_publishing_limit",
                          params={"fields": "quota_usage,config"})["data"][0]
            return f"{d.get('quota_usage')}/{d.get('config', {}).get('quota_total')}"
        except (PublishError, KeyError, IndexError):
            return "?"


# --------------------------------------------------------------------------- #
# Publicación
# --------------------------------------------------------------------------- #
def urls_publicas(fecha: date, repo: str, rama: str) -> list[str]:
    """URL raw de cada foto, comprobando antes que existe en local y en GitHub."""
    urls = []
    for i in (1, 2, 3):
        nombre = f"{fecha.isoformat()}_{i}.jpg"
        if not (OUT_DIR / nombre).exists():
            raise PublishError(f"No existe out/{nombre}. Ejecuta antes: python -m src.render --fecha {fecha}")
        url = f"https://raw.githubusercontent.com/{repo}/{rama}/out/{nombre}"
        try:
            r = requests.get(url, timeout=30)
        except requests.RequestException as e:
            raise PublishError(f"No se puede comprobar {url}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise PublishError(f"{url} da HTTP {r.status_code}. ¿Has hecho commit y push de out/{nombre}?")
        if not r.content.startswith(b"\xff\xd8"):
            raise PublishError(f"{url} no es un JPEG")
        if r.content != (OUT_DIR / nombre).read_bytes():
            raise PublishError(f"{url} no coincide con out/{nombre}: haz push de la versión actual")
        urls.append(url)
    return urls


def publicar_carrusel(fecha: date, prueba: bool = False, forzar: bool = False) -> str | None:
    cargar_env()
    previo = state.ya_publicado(fecha)
    if previo and not forzar:
        log.info("El %s ya está publicado (%s). No se repite.", fecha, previo.get("permalink"))
        return previo.get("permalink")

    repo = os.getenv("GITHUB_REPOSITORY", "angelmoslares/pvpc-instagram")
    rama = os.getenv("GITHUB_BRANCH", "main")
    ig = Instagram(os.getenv("IG_TOKEN", "").strip(), os.getenv("IG_USER_ID", "").strip(),
                   os.getenv("IG_API_VERSION", API_VERSION_DEFECTO).strip())

    pie = pie_de_foto(fecha)
    urls = urls_publicas(fecha, repo, rama)
    log.info("Fotos en GitHub OK. Host %s. Cuota de publicación usada: %s", ig.host, ig.cuota())

    hijos = []
    for url in urls:
        cid = ig.contenedor(image_url=url, is_carousel_item="true")
        ig.esperar(cid)
        hijos.append(cid)
        log.info("Foto lista: %s", url.rsplit("/", 1)[-1])

    carrusel = ig.contenedor(media_type="CAROUSEL", children=",".join(hijos), caption=pie)
    ig.esperar(carrusel)
    log.info("Carrusel listo (contenedor %s)", carrusel)

    if prueba:
        log.info("PRUEBA: no se publica. El contenedor caduca solo en 24 h.")
        return None

    media_id = ig.publicar(carrusel)
    enlace = ig.permalink(media_id)
    state.marcar(fecha, media_id, enlace, len(hijos))
    log.info("PUBLICADO: %s", enlace or media_id)
    return enlace


def _main() -> int:
    ap = argparse.ArgumentParser(description="Publica el carrusel de un día en Instagram.")
    ap.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid)")
    ap.add_argument("--prueba", action="store_true", help="Hace todo menos publicar")
    ap.add_argument("--forzar", action="store_true", help="Publica aunque el día figure como publicado")
    ap.add_argument("--pie", action="store_true", help="Solo muestra el pie de foto")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fecha = a.fecha or manana_madrid()
    try:
        if a.pie:
            print(pie_de_foto(fecha))
            return 0
        publicar_carrusel(fecha, a.prueba, a.forzar)
    except (PublishError, RuntimeError, FileNotFoundError) as e:
        log.error("ERROR: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())

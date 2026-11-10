"""
publish.py — Publica el carrusel y el reel de un día en Instagram.

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

Reel (solo con token EAA…, que es el que permite la subida resumable):
    1. Comprueba que el reel no está publicado y que out/<fecha>_reel.mp4 cumple la especificación.
    2. Reutiliza el contenedor del día si ya se subió en otro disparo (< 23 h); si no, crea un
       contenedor REELS resumable y sube el MP4 directamente a rupload.facebook.com.
    3. Espera a FINISHED, comprueba que no hay ya un reel de ese día en la cuenta y publica.

Uso en local (desde la raíz del repo, con IG_TOKEN e IG_USER_ID en .env):
    python -m src.publish --fecha 2026-11-04 --prueba   # todo menos publicar
    python -m src.publish --fecha 2026-11-04            # publica de verdad
    python -m src.publish --pie --fecha 2026-11-04      # solo muestra el pie de foto
    python -m src.publish --reel --fecha 2026-11-04 --prueba   # reel: sube y espera, sin publicar
    python -m src.publish --reel --fecha 2026-11-04            # reel: publica
    python -m src.publish --reel --pie --fecha 2026-11-04      # pie de foto del reel

El token nunca se imprime: va en la cabecera Authorization.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timezone

import requests

from src import state
from src.fetch_pvpc import ROOT, cargar_env, manana_madrid
from src.render import OUT_DIR, contexto, verificar

TIMEOUT_S = 60
TIMEOUT_VIDEO_S = 300          # subida del MP4 (≈ 1 MB, pero holgado)
REUTILIZAR_CONTENEDOR_H = 23   # los contenedores caducan a las 24 h
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
LLAMADA = {
    "carrusel": "Desliza para verlo con calma. Que la luz te acompañe… y que sea barata.",
    "reel": "Envíaselo a esa persona que necesita ahorrar. Que la luz te acompañe… y que sea barata.",
}


def pie_de_foto(fecha: date, formato: str = "carrusel") -> str:
    """Pie de foto. El del reel solo cambia la llamada: compartir en vez de deslizar."""
    if formato not in LLAMADA:
        raise ValueError(f"Formato desconocido: {formato}")
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
        LLAMADA[formato],
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

    def contenedor_reel(self, caption: str, thumb_offset_ms: int, share_to_feed: bool = True) -> tuple[str, str]:
        """Contenedor REELS de subida resumable. Devuelve (id, uri de subida)."""
        if self.host != "graph.facebook.com":
            raise PublishError("La subida resumable de vídeo solo existe con token de página EAA… "
                               "(graph.facebook.com). Con token IG… habría que alojar el MP4 y usar video_url.")
        datos = self._req("POST", f"{self.user_id}/media", data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "thumb_offset": str(thumb_offset_ms),
        })
        cid = datos["id"]
        # Meta devuelve la uri de subida; si faltara, se construye igual que en su documentación.
        uri = datos.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.base.rsplit('/', 1)[-1]}/{cid}"
        return cid, uri

    def subir_video(self, uri: str, ruta) -> None:
        """Sube el MP4 entero al contenedor (offset 0). El token va en la cabecera, nunca en la URL."""
        if not uri.startswith("https://rupload.facebook.com/"):
            raise PublishError(f"uri de subida inesperada: {uri.split('?')[0]}")
        cuerpo = ruta.read_bytes()
        try:
            # Igual que el ejemplo de Meta: Authorization (ya en la sesión), offset y file_size.
            r = self.s.post(uri, data=cuerpo, timeout=TIMEOUT_VIDEO_S, headers={
                "offset": "0",
                "file_size": str(len(cuerpo)),
            })
        except requests.RequestException as e:
            raise PublishError(f"rupload.facebook.com no responde: {type(e).__name__}") from e
        try:
            datos = r.json()
        except ValueError:
            datos = {}
        if r.status_code != 200 or not datos.get("success"):
            info = datos.get("debug_info") or datos.get("error") or r.text[:200]
            raise PublishError(f"Subida del vídeo fallida (HTTP {r.status_code}): {info}")

    def estado(self, cid: str) -> str | None:
        try:
            return self._req("GET", cid, params={"fields": "status_code"}).get("status_code")
        except PublishError:
            return None

    def medios_recientes(self, n: int = 5) -> list[dict]:
        try:
            return self._req("GET", f"{self.user_id}/media",
                             params={"fields": "id,media_product_type,timestamp,caption,permalink",
                                     "limit": str(n)}).get("data", [])
        except PublishError as e:
            log.warning("No se pudo consultar lo publicado recientemente: %s", e)
            return []

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


def _horas_desde(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except ValueError:
        return float("inf")


def publicar_reel(fecha: date, prueba: bool = False, forzar: bool = False) -> str | None:
    """Publica out/<fecha>_reel.mp4. En prueba sube y espera a FINISHED, pero no publica ni toca el estado."""
    from src import reel_ganchos, reel_guion
    from src.render_reel import validar_mp4

    cargar_env()
    previo = state.ya_publicado(fecha, "reel")
    if previo and not forzar:
        log.info("El reel del %s ya está publicado (%s). No se repite.", fecha, previo.get("permalink"))
        return previo.get("permalink")

    ruta = OUT_DIR / f"{fecha.isoformat()}_reel.mp4"
    if not ruta.exists():
        raise PublishError(f"No existe out/{ruta.name}. Ejecuta antes: python -m src.render_reel --fecha {fecha}")
    fallos = validar_mp4(ruta, reel_guion.DURACION)
    if fallos:
        raise PublishError(f"{ruta.name} no cumple la especificación de Reels: " + "; ".join(fallos))

    ig = Instagram(os.getenv("IG_TOKEN", "").strip(), os.getenv("IG_USER_ID", "").strip(),
                   os.getenv("IG_API_VERSION", API_VERSION_DEFECTO).strip())
    pie = pie_de_foto(fecha, "reel")
    ctx = contexto(fecha)
    gancho = reel_ganchos.plano(reel_ganchos.gancho(fecha, ctx))
    primera = pie.splitlines()[0]
    guardar = not prueba  # en prueba no se escribe el estado (evita conflictos con el que sube Actions)
    log.info("Reel %s (%.1f MB). Host %s. Cuota de publicación usada: %s",
             ruta.name, ruta.stat().st_size / 1e6, ig.host, ig.cuota())

    # Anti-duplicado: si la cuenta ya tiene el reel de ese día (p. ej. falló el push del estado), no se repite.
    if not forzar:
        for m in ig.medios_recientes():
            if m.get("media_product_type") == "REELS" and (m.get("caption") or "").startswith(primera):
                log.info("Ya hay un reel de ese día en la cuenta (%s). Se marca y no se repite.", m.get("permalink"))
                if guardar:
                    state.marcar_reel(fecha, media_id=m["id"], permalink=m.get("permalink"),
                                      publicado_utc=state.ahora_utc(), segundos=reel_guion.DURACION, gancho=gancho)
                return m.get("permalink")

    # Reutilizar el contenedor de un disparo anterior si el vídeo llegó a subirse y no ha caducado
    previo = state.reel(fecha)
    cid = None
    if not forzar and previo.get("contenedor") and previo.get("subido") \
            and _horas_desde(previo.get("creado_utc")) < REUTILIZAR_CONTENEDOR_H:
        codigo = ig.estado(previo["contenedor"])
        if codigo in ("FINISHED", "IN_PROGRESS"):
            cid = previo["contenedor"]
            log.info("Se reutiliza el contenedor %s (%s)", cid, codigo)
        else:
            log.info("El contenedor anterior está en %s: se crea uno nuevo", codigo)

    if not cid:
        cid, uri = ig.contenedor_reel(pie, reel_guion.PORTADA_MS)
        if guardar:
            state.marcar_reel(fecha, contenedor=cid, creado_utc=state.ahora_utc(), subido=False, gancho=gancho)
        ig.subir_video(uri, ruta)
        if guardar:
            state.marcar_reel(fecha, subido=True)
        log.info("Vídeo subido al contenedor %s", cid)

    ig.esperar(cid)
    log.info("Reel listo (contenedor %s)", cid)

    if prueba:
        log.info("PRUEBA: no se publica. El contenedor caduca solo en 24 h.")
        return None

    media_id = ig.publicar(cid)
    enlace = ig.permalink(media_id)
    state.marcar_reel(fecha, media_id=media_id, permalink=enlace, publicado_utc=state.ahora_utc(),
                      segundos=reel_guion.DURACION, gancho=gancho)
    log.info("REEL PUBLICADO: %s", enlace or media_id)
    return enlace


def _main() -> int:
    ap = argparse.ArgumentParser(description="Publica el carrusel o el reel de un día en Instagram.")
    ap.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid)")
    ap.add_argument("--prueba", action="store_true", help="Hace todo menos publicar")
    ap.add_argument("--forzar", action="store_true", help="Publica aunque el día figure como publicado")
    ap.add_argument("--pie", action="store_true", help="Solo muestra el pie de foto")
    ap.add_argument("--reel", action="store_true", help="El reel (out/<fecha>_reel.mp4) en vez del carrusel")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fecha = a.fecha or manana_madrid()
    try:
        if a.pie:
            print(pie_de_foto(fecha, "reel" if a.reel else "carrusel"))
            return 0
        if a.reel:
            publicar_reel(fecha, a.prueba, a.forzar)
        else:
            publicar_carrusel(fecha, a.prueba, a.forzar)
    except (PublishError, RuntimeError, FileNotFoundError) as e:
        log.error("ERROR: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())

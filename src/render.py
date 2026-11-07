"""
render.py — Genera las 3 fotos del carrusel (1080 x 1350, JPEG) a partir del CSV
horario de un día.

    1. Resumen: más barata, media y más cara + tira de barras del día.
    2. Lavadora: coste de un lavado en la hora más barata y en la más cara.
    3. Lavavajillas: lo mismo para el lavavajillas.

La plantilla es templates/carrusel.html + carrusel.css (Jinja2). Playwright
(Chromium headless) carga la página y captura cada <section class="slide">.

Uso en local (desde la raíz del repo):
    python -m src.render                      # D+1 (necesita data/pvpc_<D+1>.csv)
    python -m src.render --fecha 2026-11-05
    python -m src.render --fecha 2026-11-05 --html   # guarda también el HTML para depurar

Primera vez en Windows:  pip install -r requirements.txt  y  playwright install chromium
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image

from src.fetch_pvpc import TZ, manana_madrid

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
TEMPLATES = ROOT / "templates"

ANCHO, ALTO = 1080, 1350
CALIDAD_JPEG = 90
HANDLE = "@quelaluzteacompane"

# Consumo por ciclo, programa normal (medias de OCU). El coste se calcula con el
# precio de la hora más barata y de la más cara del día: las mismas de la foto 1.
ELECTRODOMESTICOS = {
    "lavadora": {"nombre": "la lavadora", "titulo": "Lavadora", "kwh": 0.9,
                 "nota": "por ciclo (algodón 40 ºC, media OCU)"},
    "lavavajillas": {"nombre": "el lavavajillas", "titulo": "Lavavajillas", "kwh": 1.0,
                     "nota": "por ciclo (programa normal, media OCU)"},
}

# Escala de los sprites: cada píxel del sprite ocupa N píxeles de la imagen final
ESCALA_RESUMEN = 6
ESCALA_ESCENA = 6

DIAS = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]

log = logging.getLogger("render")


# --------------------------------------------------------------------------- #
# Formato
# --------------------------------------------------------------------------- #
def num(x: float, dec: int) -> str:
    """Coma decimal española."""
    return f"{x:.{dec}f}".replace(".", ",")


def fecha_corta(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def rango_hora(ini: pd.Timestamp) -> str:
    """Una sola hora: 'de 14 a 15 h' (en la hora repetida de octubre evita 'de 2 a 2 h')."""
    return f"de {ini.hour} a {(ini.hour + 1) % 24} h"


# --------------------------------------------------------------------------- #
# Cálculos
# --------------------------------------------------------------------------- #
def cargar(fecha: date) -> pd.DataFrame:
    ruta = DATA_DIR / f"pvpc_{fecha.isoformat()}.csv"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe {ruta.relative_to(ROOT)}. Ejecuta antes: python -m src.fetch_pvpc --fecha {fecha}")
    df = pd.read_csv(ruta, dtype={"etiqueta": str})
    df["hora_utc"] = pd.to_datetime(df["hora_utc"], utc=True)
    df["hora_local"] = df["hora_utc"].dt.tz_convert(TZ)
    df["etiqueta"] = df["etiqueta"].str.replace("′", "'", regex=False)  # CSV antiguos
    return df.sort_values("hora_utc").reset_index(drop=True)


def tramos(precios: pd.Series) -> list[str]:
    """Tercio más barato, intermedio y más caro del día (floor(n/3) en los extremos)."""
    n, k = len(precios), len(precios) // 3
    orden = precios.sort_values(kind="stable").index.tolist()
    t = ["medio"] * n
    for i in orden[:k]:
        t[i] = "barato"
    for i in orden[n - k:]:
        t[i] = "caro"
    return t


def contexto(fecha: date) -> dict:
    df = cargar(fecha)
    p = df["precio_eur_kwh"]
    n = len(df)
    i_min, i_max = int(p.idxmin()), int(p.idxmax())
    pmax = float(p.max())
    alto_tira = 176  # px del área de barras de la foto 1

    def alto(x: float) -> int:
        return max(8, round(max(x, 0) / pmax * alto_tira / 8) * 8)

    t = tramos(p)
    barras = [{"etiqueta": e, "tramo": tr, "alto": alto(x),
               "marca": "min" if i == i_min else "max" if i == i_max else "",
               "fuerte": i % 3 == 0}
              for i, (e, tr, x) in enumerate(zip(df["etiqueta"], t, p))]

    # Precios tal y como se ven en la foto 1 (3 decimales): todo se calcula a partir de ellos
    # para que cualquiera pueda comprobar las cuentas con lo que lee.
    precio_min, precio_max = round(float(p.min()), 3), round(float(p.max()), 3)
    rango_min, rango_max = rango_hora(df.loc[i_min, "hora_local"]), rango_hora(df.loc[i_max, "hora_local"])

    escenas = {}
    for clave, e in ELECTRODOMESTICOS.items():
        c_mejor, c_peor = e["kwh"] * precio_min, e["kwh"] * precio_max
        escenas[clave] = {
            "nombre": e["nombre"],
            "mejor_rango": rango_min, "mejor_coste": num(c_mejor, 2),
            "peor_rango": rango_max, "peor_coste": num(c_peor, 2),
            "titulo": e["titulo"], "kwh": num(e["kwh"], 1), "nota": e["nota"],
        }

    return {
        "fecha": fecha_corta(fecha),
        "handle": HANDLE,
        "n_horas": n,
        "min": {"cifra": num(precio_min, 3), "rango": rango_min},
        "media": {"cifra": num(p.mean(), 3), "rango": f"{n} horas"},
        "max": {"cifra": num(precio_max, 3), "rango": rango_max},
        "media_alto": round(p.mean() / pmax * alto_tira / 8) * 8,
        "barras": barras,
        "escenas": escenas,
        "escala_resumen": ESCALA_RESUMEN,
        "escala_escena": ESCALA_ESCENA,
        "sprites": medidas_sprites(),
    }


def verificar(ctx: dict) -> None:
    """Comprueba que las tres fotos cuentan lo mismo. Si algo no cuadra, no se publica."""
    errores = []
    def a_float(s: str) -> float:
        return float(s.replace(",", "."))
    pmin, pmax = a_float(ctx["min"]["cifra"]), a_float(ctx["max"]["cifra"])
    if not pmin <= a_float(ctx["media"]["cifra"]) <= pmax:
        errores.append("la media no está entre el mínimo y el máximo")
    marcas = {b["marca"]: b["etiqueta"] for b in ctx["barras"] if b["marca"]}
    for tipo, d in (("min", ctx["min"]), ("max", ctx["max"])):
        hora = int(d["rango"].split()[1])
        if tipo not in marcas or int(marcas[tipo].rstrip("'")) != hora:
            errores.append(f"la marca '{tipo}' de las barras no está en la hora {hora}")
    for clave, e in ctx["escenas"].items():
        kwh = ELECTRODOMESTICOS[clave]["kwh"]
        if e["mejor_rango"] != ctx["min"]["rango"]:
            errores.append(f"{clave}: hora barata '{e['mejor_rango']}' distinta de la foto 1 '{ctx['min']['rango']}'")
        if e["peor_rango"] != ctx["max"]["rango"]:
            errores.append(f"{clave}: hora cara '{e['peor_rango']}' distinta de la foto 1 '{ctx['max']['rango']}'")
        if e["mejor_coste"] != num(kwh * pmin, 2) or e["peor_coste"] != num(kwh * pmax, 2):
            errores.append(f"{clave}: los costes no salen de {kwh} kWh x precio de la foto 1")
    if errores:
        raise RuntimeError("Las fotos no cuadran: " + "; ".join(errores))


def medidas_sprites() -> dict:
    """Tamaño natural de cada sprite, para escalarlo por un factor entero sin suavizado."""
    out = {}
    for ruta in [*(ROOT / "assets/personaje").glob("*.png"), *(ROOT / "assets/objetos").glob("*.png")]:
        with Image.open(ruta) as im:
            out[ruta.stem] = {"w": im.width, "h": im.height}
    return out


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def html_carrusel(ctx: dict) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined, autoescape=True)
    return env.get_template("carrusel.html").render(base=ROOT.as_uri() + "/", **ctx)


def render(fecha: date | None = None, guardar_html: bool = False) -> list[Path]:
    from playwright.sync_api import sync_playwright

    fecha = fecha or manana_madrid()
    ctx = contexto(fecha)
    verificar(ctx)
    html = html_carrusel(ctx)

    OUT_DIR.mkdir(exist_ok=True)
    tmp = OUT_DIR / f"_{fecha.isoformat()}.html"  # junto al repo para que <base> y file:// funcionen
    tmp.write_text(html, encoding="utf-8")

    rutas = []
    try:
        with sync_playwright() as pw:
            nav = pw.chromium.launch()
            pag = nav.new_page(viewport={"width": ANCHO, "height": ALTO}, device_scale_factor=1)
            pag.goto(tmp.as_uri(), wait_until="load")
            pag.evaluate("document.fonts.ready")
            faltan = pag.evaluate("""() => ['Press Start 2P', 'Silkscreen', 'VT323']
                .filter(f => !document.fonts.check('16px "' + f + '"'))""")
            if faltan:
                raise RuntimeError(f"Fuentes sin cargar: {faltan}. ¿Están en assets/fonts/?")
            rotas = pag.evaluate("() => [...document.images].filter(i => !i.naturalWidth).map(i => i.src)")
            if rotas:
                raise RuntimeError(f"Imágenes sin cargar: {rotas}")
            for i, slide in enumerate(pag.locator("section.slide").all(), start=1):
                ruta = OUT_DIR / f"{fecha.isoformat()}_{i}.jpg"
                slide.screenshot(path=str(ruta), type="jpeg", quality=CALIDAD_JPEG)
                rutas.append(ruta)
            nav.close()
    finally:
        if not guardar_html:
            tmp.unlink(missing_ok=True)

    for r in rutas:
        with Image.open(r) as im:
            if im.size != (ANCHO, ALTO) or im.format != "JPEG":
                raise RuntimeError(f"{r.name}: {im.format} {im.size}, se esperaba JPEG {ANCHO}x{ALTO}")
    return rutas


def _main() -> int:
    ap = argparse.ArgumentParser(description="Genera las fotos del carrusel de un día.")
    ap.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid)")
    ap.add_argument("--html", action="store_true", help="Conserva el HTML generado en out/ para depurar")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        for r in render(a.fecha, a.html):
            log.info("OK %s", r.relative_to(ROOT))
    except (FileNotFoundError, RuntimeError) as e:
        log.error("ERROR: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())

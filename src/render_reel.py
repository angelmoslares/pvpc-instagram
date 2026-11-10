"""
render_reel.py — Genera el reel del día (1080 × 1920, MP4) a partir de los mismos datos
y componentes que el carrusel.

    1. contexto(fecha) + verificar(ctx): los mismos números que las fotos.
    2. templates/reel.html (Jinja, componentes de _componentes.html) → out/_reel/<fecha>/
       con copias de carrusel.css, reel.css, fuentes, sprites y GSAP.
    3. HyperFrames (local, sin cuenta ni nube): lint y render a MP4.
    4. Remux para Instagram: pista AAC 48 kHz, sin edit lists, moov al principio.
    5. validar_mp4(): si no cumple la especificación de Reels, error.
    6. --fidelidad: compara píxel a píxel fichas, tira y bocadillos con el carrusel.

Uso en local (raíz del repo, entorno de Anaconda activo):
    python -m src.render_reel --fecha 2026-11-10
    python -m src.render_reel --fecha 2026-11-10 --fidelidad
    python -m src.render_reel --fecha 2026-11-10 --borrador        # calidad draft, más rápido

Primera vez:  npm install   (Node 22+, instala hyperframes y gsap en versión fija)
              FFmpeg con ffprobe en el PATH, o HYPERFRAMES_FFMPEG_PATH / FFPROBE_PATH
"""

from __future__ import annotations

import argparse
import glob
import io
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import date
from pathlib import Path

import imageio_ffmpeg
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image, ImageChops

from src import reel_ganchos, reel_guion
from src.fetch_pvpc import manana_madrid
from src.render import OUT_DIR, ROOT, TEMPLATES, contexto, html_carrusel, verificar

ANCHO, ALTO = 1080, 1920
MAX_BYTES = 300 * 1024 * 1024
log = logging.getLogger("render_reel")


class ReelError(Exception):
    pass


# --------------------------------------------------------------------------- #
# 1-2 · Composición
# --------------------------------------------------------------------------- #
def dir_reel(fecha: date) -> Path:
    return OUT_DIR / "_reel" / fecha.isoformat()


def html_reel(ctx: dict, ctx_gancho) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined, autoescape=True)
    return env.get_template("reel.html").render(guion=reel_guion.para_plantilla(), gancho=ctx_gancho, **ctx)


def construir(fecha: date) -> tuple[Path, dict]:
    """Escribe la composición de HyperFrames con todo lo que necesita dentro de su carpeta."""
    ctx = contexto(fecha)
    verificar(ctx)
    d = dir_reel(fecha)
    if d.exists():
        shutil.rmtree(d)
    (d / "templates").mkdir(parents=True)
    for css in ("carrusel.css", "reel.css"):
        shutil.copy2(TEMPLATES / css, d / "templates" / css)
    for sub in ("fonts", "personaje", "objetos"):
        (d / "assets" / sub).mkdir(parents=True)
        for f in (ROOT / "assets" / sub).iterdir():
            if f.is_file() and f.suffix.lower() in (".ttf", ".png"):
                shutil.copy2(f, d / "assets" / sub / f.name)
    gsap = ROOT / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    if not gsap.exists():
        raise ReelError("Falta node_modules/gsap. Ejecuta antes: npm install")
    (d / "assets" / "vendor").mkdir(parents=True)
    shutil.copy2(gsap, d / "assets" / "vendor" / "gsap.min.js")
    texto = reel_ganchos.gancho(fecha, ctx)
    log.info("Gancho del día: %s", reel_ganchos.plano(texto))
    (d / "index.html").write_text(html_reel(ctx, reel_ganchos.a_html(texto)), encoding="utf-8")
    return d, ctx


# --------------------------------------------------------------------------- #
# 3 · HyperFrames (siempre local)
# --------------------------------------------------------------------------- #
def navegador_playwright() -> str | None:
    """chrome-headless-shell que ya instala Playwright: HyperFrames lo usa en modo rápido (BeginFrame)."""
    bases = [os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")]
    if os.name == "nt":
        bases.append(os.path.join(os.getenv("LOCALAPPDATA", ""), "ms-playwright"))
    else:
        bases.append(os.path.expanduser("~/.cache/ms-playwright"))
    nombre = "chrome-headless-shell.exe" if os.name == "nt" else "chrome-headless-shell"
    for base in filter(None, bases):
        hallados = sorted(glob.glob(os.path.join(base, "chromium_headless_shell-*", "*", nombre)))
        if hallados:
            return hallados[-1]
    return None


def entorno_hf() -> dict:
    env = dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1", DO_NOT_TRACK="1")
    if not env.get("HYPERFRAMES_BROWSER_PATH"):
        nav = navegador_playwright()
        if nav:
            env["HYPERFRAMES_BROWSER_PATH"] = nav
    return env


def hyperframes(*args: str, cwd: Path, timeout: int = 900) -> str:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise ReelError("No encuentro npx: instala Node.js 22 o superior.")
    r = subprocess.run([npx, "--no-install", "hyperframes", *args], cwd=cwd, env=entorno_hf(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    salida = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise ReelError(f"hyperframes {args[0]} falló (código {r.returncode}):\n{salida[-2000:]}")
    return salida


# --------------------------------------------------------------------------- #
# 4-5 · MP4 para Instagram
# --------------------------------------------------------------------------- #
def ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def remux(origen: Path, destino: Path, segundos: float) -> None:
    """Sin recodificar el vídeo: añade audio AAC (silencio), quita edit lists y pone moov al principio."""
    cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(origen),
           "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-map", "0:v:0", "-map", "1:a:0", "-t", f"{segundos:.3f}",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", "-use_editlist", "0", str(destino)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ReelError(f"ffmpeg (remux) falló: {r.stderr[-800:]}")


def _cajas(datos: bytes, ini: int, fin: int) -> list[tuple[str, int, int]]:
    out = []
    while ini + 8 <= fin:
        tam, tipo = struct.unpack(">I4s", datos[ini:ini + 8])
        if tam == 1:
            tam = struct.unpack(">Q", datos[ini + 8:ini + 16])[0]
        elif tam == 0:
            tam = fin - ini
        if tam < 8:
            break
        out.append((tipo.decode("latin1"), ini, tam))
        ini += tam
    return out


def validar_mp4(ruta: Path, segundos: float) -> list[str]:
    """Especificación de Reels de Meta (IG User Media). Devuelve la lista de fallos."""
    fallos = []
    datos = ruta.read_bytes()
    if len(datos) > MAX_BYTES:
        fallos.append(f"pesa {len(datos) / 1e6:.0f} MB (máx. 300)")
    cajas = _cajas(datos, 0, len(datos))
    tipos = [t for t, _, _ in cajas]
    if "moov" not in tipos or "mdat" not in tipos or tipos.index("moov") > tipos.index("mdat"):
        fallos.append(f"moov no está antes de mdat ({tipos})")
    else:
        _, ini, tam = cajas[tipos.index("moov")]
        for t, i, s in _cajas(datos, ini + 8, ini + tam):
            if t == "trak" and any(x == "edts" for x, _, _ in _cajas(datos, i + 8, i + s)):
                fallos.append("hay edit lists (edts)")
                break
    info = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(ruta)], capture_output=True, text=True).stderr
    v = re.search(r"Video: (\w+).*?, (\w+)\(?.*?, (\d+)x(\d+).*?, ([\d.]+) fps", info)
    a = re.search(r"Audio: (\w+).*?, (\d+) Hz, (\w+)", info)
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    if not v:
        fallos.append("sin pista de vídeo legible")
    else:
        codec, pix, w, h, fps = v.group(1), v.group(2), int(v.group(3)), int(v.group(4)), float(v.group(5))
        if codec not in ("h264", "hevc"):
            fallos.append(f"códec de vídeo {codec}")
        if pix != "yuv420p":
            fallos.append(f"submuestreo {pix} (se pide 4:2:0)")
        if (w, h) != (ANCHO, ALTO):
            fallos.append(f"tamaño {w}x{h}")
        if not 23 <= fps <= 60:
            fallos.append(f"{fps} fps")
    if not a:
        fallos.append("sin pista de audio")
    elif a.group(1) != "aac" or int(a.group(2)) > 48000:
        fallos.append(f"audio {a.group(1)} {a.group(2)} Hz")
    if dur:
        s = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))
        if not 3 <= s <= 900:
            fallos.append(f"dura {s:.1f} s")
        if abs(s - segundos) > 0.2:
            fallos.append(f"dura {s:.2f} s y el guion {segundos} s")
    return fallos


# --------------------------------------------------------------------------- #
# 6 · Fidelidad
# --------------------------------------------------------------------------- #
def fidelidad(fecha: date, d: Path, ctx: dict) -> list[str]:
    """Compara cada componente del reel (instantánea PNG de HyperFrames, sin pérdidas) con el
    mismo componente del carrusel renderizado por Playwright. Devuelve los que difieren."""
    from playwright.sync_api import sync_playwright

    controles = reel_guion.CONTROL_FIDELIDAD
    instantes = sorted({t for _, t, _, tipo in controles if tipo == "pixel"})
    snap_dir = d / "_fidelidad"
    shutil.rmtree(snap_dir, ignore_errors=True)
    shutil.rmtree(d / ".hyperframes" / "artifacts" / "snapshots", ignore_errors=True)
    hyperframes("snapshot", "--at", ",".join(f"{t:g}" for t in instantes), cwd=d)
    # HyperFrames guarda frame-NN-at-<t>s.png en una carpeta por ejecución
    hallados = {}
    for p in (d / ".hyperframes" / "artifacts" / "snapshots").rglob("frame-*-at-*s.png"):
        m = re.search(r"-at-([\d.]+)s\.png$", p.name)
        if m:
            hallados[round(float(m.group(1)), 3)] = p
    snap_dir.mkdir()
    por_t = {}
    for t in instantes:
        p = hallados.get(round(t, 3))
        if not p:
            raise ReelError(f"snapshot no generó el instante {t} s (hay {sorted(hallados)})")
        destino = snap_dir / f"reel_{t:g}s.png"
        shutil.copy2(p, destino)
        por_t[t] = Image.open(destino).convert("RGB")
        if por_t[t].size != (ANCHO, ALTO):
            raise ReelError(f"La instantánea de {t} s mide {por_t[t].size}")

    tmp = OUT_DIR / f"_fid_{fecha.isoformat()}.html"
    tmp.write_text(html_carrusel(ctx), encoding="utf-8")
    fallos = []
    try:
        with sync_playwright() as pw:
            nav = pw.chromium.launch()
            # Carrusel: cada componente tal y como sale en las fotos (PNG, sin JPEG)
            pag = nav.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
            pag.goto(tmp.as_uri(), wait_until="load")
            pag.evaluate("document.fonts.ready")
            slide = {"fichas": ".resumen", "tira": ".resumen",
                     "lavadora": ".escena-lavadora", "lavavajillas": ".escena-lavavajillas"}
            # Reel: posición de cada componente en el instante de control
            reel = nav.new_page(viewport={"width": ANCHO, "height": ALTO}, device_scale_factor=1)
            reel.goto((d / "index.html").as_uri(), wait_until="load")
            # La línea de tiempo se registra tras cargar las fuentes. Nunca devolver el objeto
            # de GSAP a Python (es enorme y cíclico): de ahí el «, true» / «, null».
            reel.wait_for_function("!!(window.__timelines && window.__timelines.main)", timeout=30000)
            for esc, t, sel, tipo in controles:
                nombre = f"{esc} {sel}"
                reel.evaluate(f"window.__timelines.main.seek({t}, false), null")  # false: que salten los onUpdate
                if tipo == "texto":
                    # Letra más grande en el reel: se exige el mismo contenido, no los mismos píxeles
                    limpio = "(e) => e.innerText.replace(/\\s+/g, ' ').trim()"
                    a_ = pag.locator(f"{slide[esc]} {sel}").first.evaluate(limpio)
                    b_ = reel.locator(f"#esc-{esc} {sel}").first.evaluate(limpio)
                    if a_ != b_:
                        fallos.append(f"{nombre}: carrusel «{a_}» ≠ reel «{b_}»")
                    log.info("Fidelidad %-32s %s", nombre, "mismo texto" if a_ == b_ else "TEXTO DISTINTO")
                    continue
                ref = Image.open(io.BytesIO(pag.locator(f"{slide[esc]} {sel}").first.screenshot(type="png"))).convert("RGB")
                caja = reel.locator(f"#esc-{esc} {sel}").first.bounding_box()
                x, y = round(caja["x"]), round(caja["y"])
                recorte = por_t[t].crop((x, y, x + ref.width, y + ref.height))
                diff = ImageChops.difference(ref, recorte)
                bbox = diff.getbbox()
                if bbox:
                    px = sum(1 for p in diff.getdata() if max(p) > 0)
                    fallos.append(f"{nombre}: {px} píxeles distintos en {bbox}")
                    ref.save(snap_dir / f"{esc}{sel.replace('.', '_')}_carrusel.png")
                    recorte.save(snap_dir / f"{esc}{sel.replace('.', '_')}_reel.png")
                log.info("Fidelidad %-32s %s", nombre, "DIFERENTE" if bbox else "idéntico")
            nav.close()
    finally:
        tmp.unlink(missing_ok=True)
    return fallos


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #
def render_reel(fecha: date | None = None, borrador: bool = False, comprobar: bool = False) -> Path:
    fecha = fecha or manana_madrid()
    d, ctx = construir(fecha)
    log.info("Composición: %s", d.relative_to(ROOT))

    salida_lint = hyperframes("lint", cwd=d, timeout=180)
    if re.search(r"[1-9]\d* error", salida_lint):
        raise ReelError("hyperframes lint:\n" + salida_lint[-1500:])

    bruto = d / "bruto.mp4"
    hyperframes("render", "--quality", "draft" if borrador else "looks", "--fps", str(reel_guion.FPS), "-o", str(bruto), cwd=d)

    final = OUT_DIR / f"{fecha.isoformat()}_reel.mp4"
    remux(bruto, final, reel_guion.DURACION)
    fallos = validar_mp4(final, reel_guion.DURACION)
    if fallos:
        raise ReelError(f"{final.name} no cumple la especificación de Reels: " + "; ".join(fallos))
    log.info("MP4 OK: %s (%.1f MB)", final.relative_to(ROOT), final.stat().st_size / 1e6)

    if comprobar:
        fallos = fidelidad(fecha, d, ctx)
        if fallos:
            raise ReelError("El reel no es fiel al carrusel: " + "; ".join(fallos))
        log.info("Fidelidad OK: mismos datos y componentes que el carrusel")
    return final


def _main() -> int:
    ap = argparse.ArgumentParser(description="Genera el reel de un día con HyperFrames (local).")
    ap.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid)")
    ap.add_argument("--borrador", action="store_true", help="Calidad draft para iterar")
    ap.add_argument("--fidelidad", action="store_true", help="Compara los componentes con el carrusel")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        render_reel(a.fecha, a.borrador, a.fidelidad)
    except (ReelError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("ERROR: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())

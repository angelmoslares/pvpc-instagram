"""
procesar_sprites.py — Convierte las imágenes generadas con ChatGPT en sprites
pixel art limpios con fondo transparente.

Se ejecuta UNA VEZ cada vez que cambian las imágenes originales; el proceso
diario no lo usa. Necesita scipy (viene con Anaconda; si no:
pip install scipy).

Entradas (no se tocan):
    assets/personaje/raw/poses.jpg     6 poses en rejilla 2 x 3, fondo ajedrezado
    assets/objetos/raw/objetos.jpg     7 electrodomésticos en 2 filas, fondo verde #00FF00

Salidas (PNG RGBA a resolución de píxel real; se escalan en la plantilla con
image-rendering: pixelated y un factor entero):
    assets/personaje/{senala,guino,atencion,piensa,lupa,saluda}.png
    assets/objetos/{lavadora,lavavajillas,microondas,cafetera,frigorifico,horno,tostadora}.png

Pasos: detectar la rejilla de píxel, muestrear el centro de cada píxel
(mediana 3x3, quita el ruido JPEG), quitar fondo, limpiar el halo claro del
borde, reducir a 64 colores planos y recortar cada sprite.

Uso (desde la raíz del repo):
    python tools/procesar_sprites.py             # genera los que falten desde raw/
    python tools/procesar_sprites.py --limpiar   # limpia píxeles muertos de los PNG actuales (una vez tras cada retoque)
    python tools/procesar_sprites.py --forzar    # regenera todo desde raw/ (pisa retoques a mano)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parents[1]

# Paso de rejilla y fase medidos sobre las imágenes actuales (px de la imagen
# original por píxel de arte). Si regeneras las imágenes, ajusta con detectar_rejilla().
PERSONAJE = dict(px=8.67 / 2, py=8.87 / 2, phx=6.25, phy=8.25)
OBJETOS = dict(px=8.73 / 2, py=8.74 / 2, phx=2.25, phy=3.25)

POSES = ["senala", "guino", "atencion", "piensa", "lupa", "saluda"]  # fila 1 y fila 2, de izquierda a derecha


def detectar_rejilla(path: Path) -> dict:
    """Busca paso y fase que mejor alinean los bordes. Útil si cambian las imágenes."""
    a = np.asarray(Image.open(path).convert("L")).astype(float)

    def mejor(perfil):
        res = []
        for p in np.arange(3.5, 10.0, 0.01):
            for ph in np.arange(0, p, 0.25):
                idx = np.round(ph + np.arange(0, (len(perfil) - ph) / p) * p).astype(int)
                idx = idx[idx < len(perfil)]
                res.append((perfil[idx].mean(), p, ph))
        return max(res)[1:]

    px, phx = mejor(np.abs(np.diff(a, axis=1)).sum(axis=0))
    py, phy = mejor(np.abs(np.diff(a, axis=0)).sum(axis=1))
    return dict(px=px, py=py, phx=phx, phy=phy)


def muestrear(path: Path, px: float, py: float, phx: float, phy: float) -> np.ndarray:
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    nx, ny = int((a.shape[1] - phx) / px), int((a.shape[0] - phy) / py)
    out = np.zeros((ny, nx, 3), np.uint8)
    for j in range(ny):
        y = int(phy + (j + 0.5) * py)
        for i in range(nx):
            x = int(phx + (i + 0.5) * px)
            out[j, i] = np.median(a[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3), axis=0)
    return out


def cuantizar(rgb: np.ndarray, mask: np.ndarray, n: int) -> np.ndarray:
    pts = rgb[mask]
    im = Image.fromarray(pts.reshape(1, -1, 3)).quantize(
        colors=n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = np.array(im.getpalette()[:3 * n]).reshape(-1, 3)
    out = rgb.copy()
    out[mask] = pal[np.asarray(im).reshape(-1)]
    return out


K4 = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]])


def limpiar(a: np.ndarray, personaje: bool = True) -> tuple[np.ndarray, int]:
    """Quita píxeles muertos de un sprite RGBA: halo del JPEG pegado al contorno,
    píxeles sueltos y, solo en el personaje, la mezcla gris azulada del ajedrezado y
    las motas claras dentro del pelo (en los objetos estropearía rejillas y cristales)."""
    a = a.copy(); quitados = 0
    for _ in range(40):
        m = a[...,3] > 0
        val = a[...,:3].max(-1).astype(int)
        oscuro = m & (val < 50)
        borde = m & ndi.binary_dilation(~m, structure=K4)
        vec_fg = ndi.convolve(m.astype(int), K4, mode="constant")
        vec_osc = ndi.convolve(oscuro.astype(int), K4, mode="constant")
        halo = borde & (val >= 50) & (vec_osc >= 1) & (vec_fg <= 2)
        suelto = m & (ndi.convolve(m.astype(int), np.ones((3,3)), mode="constant") - 1 <= 1)
        rgb = a[...,:3].astype(int); r, g, b = rgb[...,0], rgb[...,1], rgb[...,2]
        filas = np.arange(a.shape[0])[:, None] < a.shape[0] - 12          # fuera de las zapatillas
        teal = borde & (val >= 110) & (val <= 185) & (b > r + 20) & filas   # mezcla JPEG ajedrezado-contorno
        q = halo | suelto | (teal if personaje else False)
        if not q.any(): break
        a[q] = 0; quitados += int(q.sum())
    if not personaje:
        return a, quitados
    # motas claras dentro del pelo: grises claros rodeados de oscuro y sin piel alrededor
    m = a[...,3] > 0
    rgb = a[...,:3].astype(int); val = rgb.max(-1); sat = val - rgb.min(-1)
    piel = m & (rgb[...,0] > 180) & (rgb[...,1] > 110) & (rgb[...,2] < 160) & (sat > 60)
    osc = m & (val < 110)
    K8 = np.ones((3, 3)); K8[1, 1] = 0
    n_piel = ndi.convolve(piel.astype(int), K8, mode="constant")
    n_osc = ndi.convolve(osc.astype(int), K8, mode="constant")
    mota = m & (val > 140) & (sat < 60) & (n_piel == 0) & (n_osc >= 5)
    for y, x in zip(*np.nonzero(mota)):
        vec = [a[yy, xx, :3] for yy in range(y-1, y+2) for xx in range(x-1, x+2)
               if 0 <= yy < a.shape[0] and 0 <= xx < a.shape[1] and osc[yy, xx]]
        a[y, x, :3] = np.median(np.array(vec), axis=0).astype(np.uint8)
    return a, quitados + int(mota.sum())


def guardar(rgb, fg, celdas, nombres, carpeta: Path):
    carpeta.mkdir(parents=True, exist_ok=True)
    lab, n = ndi.label(fg, structure=np.ones((3, 3)))
    H, W = fg.shape
    for (r0, r1, c0, c1), nombre in zip(celdas, nombres):
        sel = np.zeros_like(fg)
        for k in range(1, n + 1):
            ys, xs = np.nonzero(lab == k)
            if len(ys) > 3 and r0 * H <= ys.mean() < r1 * H and c0 * W <= xs.mean() < c1 * W:
                sel |= lab == k
        ys, xs = np.nonzero(sel)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        rgba = np.zeros((y1 - y0 + 2, x1 - x0 + 2, 4), np.uint8)
        rgba[1:-1, 1:-1, :3] = rgb[y0:y1, x0:x1]
        rgba[1:-1, 1:-1, 3] = sel[y0:y1, x0:x1] * 255
        ruta = carpeta / f"{nombre}.png"
        if ruta.exists() and not FORZAR:
            print(f"{ruta.relative_to(ROOT)}: ya existe (retocado a mano?), no se toca. Usa --forzar para regenerarlo.")
            continue
        rgba, _ = limpiar(rgba, personaje=carpeta.name == "personaje")
        Image.fromarray(rgba, "RGBA").save(ruta)
        print(f"{ruta.relative_to(ROOT)}: {rgba.shape[1]} x {rgba.shape[0]} px")


def personaje():
    g = muestrear(ROOT / "assets/personaje/raw/poses.jpg", **PERSONAJE)
    sat = g.max(-1).astype(int) - g.min(-1).astype(int)
    val = g.max(-1)
    lum = g.mean(-1)

    # Fondo ajedrezado: gris claro sin saturación. Se quita lo que toca el borde
    # y los huecos interiores que tienen las dos tonalidades del ajedrezado
    # (así no se borran el blanco de los ojos ni los dientes).
    cand = (sat < 18) & (val > 195)
    lab, n = ndi.label(cand)
    borde = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    fondo = np.zeros_like(cand)
    for k in range(1, n + 1):
        m = lab == k
        if k in borde or (m.sum() >= 6 and (lum[m] < 232).sum() >= 2 and (lum[m] > 242).sum() >= 2):
            fondo |= m
    fg = ~fondo
    fg = ndi.binary_opening(fg, iterations=1) | (fg & ndi.binary_erosion(fg))

    for _ in range(2):  # halo claro del JPEG alrededor del contorno
        fg &= ~(fg & ndi.binary_dilation(~fg) & (val > 140) & (sat < 50))
    vecinos = ndi.convolve(fg.astype(int), np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]]), mode="constant")
    fg &= ~((vecinos <= 1) & (val > 100))  # píxeles sueltos

    celdas = [(0, .5, 0, 1 / 3), (0, .5, 1 / 3, 2 / 3), (0, .5, 2 / 3, 1),
              (.5, 1, 0, 1 / 3), (.5, 1, 1 / 3, 2 / 3), (.5, 1, 2 / 3, 1)]
    guardar(cuantizar(g, fg, 64), fg, celdas, POSES, ROOT / "assets/personaje")


def objetos():
    """7 electrodomésticos sobre verde: fila 1 lavadora, lavavajillas, microondas, cafetera;
    fila 2 frigorífico, horno, tostadora."""
    from scipy.cluster.vq import kmeans2

    g = muestrear(ROOT / "assets/objetos/raw/objetos.jpg", **OBJETOS)
    r, v, b = (g[..., i].astype(int) for i in range(3))

    # Fondo = verde chillón conectado con el borde (así no se borra la verdura del frigorífico)
    verde = (v > 140) & (v - np.maximum(r, b) > 70)
    lab, n = ndi.label(verde)
    borde = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    fondo = np.isin(lab, list(borde))
    for k in range(1, n + 1):  # huecos interiores del mismo verde (asa de la taza...)
        m = lab == k
        if k not in borde and m.sum() >= 4 and np.median(v[m]) > 180:
            fondo |= m
    fg = ~fondo
    for _ in range(3):  # quita el verde que se ha mezclado con el contorno
        fg &= ~(fg & ndi.binary_dilation(~fg, structure=K4) & (v > r + 25) & (v > b + 25))
    fg &= ~(ndi.convolve(fg.astype(int), np.ones((3, 3)), mode="constant") - 1 <= 1)

    # k-means conserva mejor los colores minoritarios (ropa magenta, verduras) que median cut
    cent, etq = kmeans2(g[fg].astype(float), 48, minit="++", seed=0, iter=30)
    q = g.copy()
    q[fg] = np.clip(cent[etq], 0, 255).astype(np.uint8)

    # Cada objeto = componente grande; las piezas sueltas (vapor) van al más cercano
    lab, n = ndi.label(fg, structure=np.ones((3, 3)))
    tam = np.bincount(lab.ravel())
    grandes = [k for k in range(1, n + 1) if tam[k] > 300]
    caja = {}
    for k in grandes:
        ys, xs = np.nonzero(lab == k)
        caja[k] = (ys.mean(), xs.mean(), ys.min(), ys.max(), xs.min(), xs.max())
    H = fg.shape[0]
    fila1 = sorted([k for k in grandes if caja[k][0] < H / 2], key=lambda k: caja[k][1])
    fila2 = sorted([k for k in grandes if caja[k][0] >= H / 2], key=lambda k: caja[k][1])
    nombres = dict(zip(fila1, ["lavadora", "lavavajillas", "microondas", "cafetera"]))
    nombres |= dict(zip(fila2, ["frigorifico", "horno", "tostadora"]))
    grupo = {k: lab == k for k in grandes}
    for k in range(1, n + 1):
        if k in grandes or tam[k] < 4:
            continue
        ys, xs = np.nonzero(lab == k)
        cy, cx = ys.mean(), xs.mean()
        def dist(o):
            _, _, y0, y1, x0, x1 = caja[o]
            return max(y0 - cy, 0, cy - y1) + max(x0 - cx, 0, cx - x1)
        cerca = min(grandes, key=dist)
        if dist(cerca) < 12:
            grupo[cerca] |= lab == k

    carpeta = ROOT / "assets/objetos"
    for k, nombre in nombres.items():
        sel = grupo[k]
        ys, xs = np.nonzero(sel)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        rgba = np.zeros((y1 - y0 + 2, x1 - x0 + 2, 4), np.uint8)
        rgba[1:-1, 1:-1, :3] = q[y0:y1, x0:x1]
        rgba[1:-1, 1:-1, 3] = sel[y0:y1, x0:x1] * 255
        ruta = carpeta / f"{nombre}.png"
        if ruta.exists() and not FORZAR:
            print(f"{ruta.relative_to(ROOT)}: ya existe (retocado a mano?), no se toca. Usa --forzar para regenerarlo.")
            continue
        Image.fromarray(rgba, "RGBA").save(ruta)
        print(f"{ruta.relative_to(ROOT)}: {rgba.shape[1]} x {rgba.shape[0]} px")


def limpiar_existentes():
    """Limpia en su sitio los PNG actuales (respeta los retoques a mano: solo quita píxeles muertos)."""
    for ruta in sorted([*(ROOT / "assets/personaje").glob("*.png"), *(ROOT / "assets/objetos").glob("*.png")]):
        a = np.asarray(Image.open(ruta).convert("RGBA"))
        b, n = limpiar(a, personaje=ruta.parent.name == "personaje")
        if n:
            Image.fromarray(b, "RGBA").save(ruta)
        print(f"{ruta.relative_to(ROOT)}: {n} píxeles corregidos")


FORZAR = False

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Genera o limpia los sprites del personaje y los objetos.")
    ap.add_argument("--limpiar", action="store_true", help="Solo limpia los PNG actuales en su sitio")
    ap.add_argument("--forzar", action="store_true", help="Regenera desde raw/ aunque los PNG existan (pisa retoques)")
    args = ap.parse_args()
    if args.limpiar:
        limpiar_existentes()
    else:
        FORZAR = args.forzar
        personaje()
        objetos()

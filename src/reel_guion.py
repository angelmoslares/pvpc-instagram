"""
reel_guion.py — Tiempos y textos propios del reel en un solo sitio.

render_reel.py los pasa a la plantilla (data-start / data-duration de cada escena y
posiciones de la línea de tiempo GSAP) y los usa para comprobar la fidelidad en el
instante en que cada componente ya está completo.
"""

from __future__ import annotations

FPS = 30

# Escenas en orden: (clave, inicio_s, duración_s)
ESCENAS = [
    ("entrada", 0.0, 2.5),        # gancho: el avatar pregunta
    ("fichas", 2.5, 5.0),         # la última cifra queda fija ≥ 3 s
    ("tira", 7.5, 5.0),
    ("lavadora", 12.5, 6.0),
    ("lavavajillas", 18.5, 6.0),
    ("cierre", 24.5, 3.5),        # llamada a compartir
]
DURACION = ESCENAS[-1][1] + ESCENAS[-1][2]  # 28 s

# Portada (thumb_offset): las tres fichas completas y quietas
PORTADA_MS = 6500

# Segmentos de la barra de progreso = páginas del carrusel: (inicio, fin)
PROGRESO = [(0.0, 12.5), (12.5, 18.5), (18.5, 24.5)]

# Frases del avatar que solo existen en el reel (el resto de textos sale del carrusel).
# El gancho de la entrada cambia cada día: ver src/reel_ganchos.py
TEXTOS = {
    "tira_1": "Así queda el día, hora a hora. ¡Evita las horas ",
    "tira_color": "en rosa",
    "tira_2": "!",
    "cierre": "Envíaselo a esa persona que necesita ahorrar",
    "acciones": ["Me gusta", "Guardar", "Enviar"],
}

# Comprobación de fidelidad contra el carrusel, con cada componente completo y quieto:
#   (escena, instante_s, selector, tipo)
#   "pixel": idéntico píxel a píxel · "texto": mismos textos y cifras (en la escena de
#   fichas la letra es más grande que en el carrusel, así que no puede ser idéntica).
CONTROL_FIDELIDAD = [
    ("fichas", 7.3, ".ficha.barato", "texto"),
    ("fichas", 7.3, ".ficha.media", "texto"),
    ("fichas", 7.3, ".ficha.caro", "texto"),
    ("tira", 12.3, ".tira", "pixel"),
    ("lavadora", 18.3, ".bocadillo", "pixel"),
    ("lavavajillas", 24.3, ".bocadillo", "pixel"),
]


def escena(clave: str) -> dict:
    for k, ini, dur in ESCENAS:
        if k == clave:
            return {"clave": k, "inicio": ini, "duracion": dur, "fin": ini + dur}
    raise KeyError(clave)


def para_plantilla() -> dict:
    return {
        "escenas": {k: escena(k) for k, _, _ in ESCENAS},
        "duracion": DURACION,
        "fps": FPS,
        "progreso": PROGRESO,
        "textos": TEXTOS,
    }

"""
reel_ganchos.py — Gancho del reel: una frase distinta cada día, siempre con un dato del día.

Reglas (skill ig-reel): sin saludo, con una cifra comprobable, corta y dirigida a quien mira.
Todas las cifras salen de los textos que ya muestra el carrusel (contexto de src.render):
aquí no se recalcula ningún precio, solo se comparan los costes ya redondeados.

Rotación: hay 8 plantillas y se elige por el número de día (ordinal % 8), así que el mismo
gancho no cae siempre en el mismo día de la semana. Si la plantilla del día no es válida con
los datos (p. ej. «más del doble» cuando no lo es), se pasa a la siguiente.

Marcas de color en el texto: [b]…[/b] = barato (cian) · [c]…[/c] = caro (magenta).

Prueba:  python -m src.reel_ganchos --fecha 2026-11-10        (lista las 8 y marca la elegida)
"""

from __future__ import annotations

import argparse
from datetime import date

from markupsafe import Markup, escape

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _num(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


def _eur(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _mas_caro(mejor: float, peor: float) -> str | None:
    """Cómo decir cuánto más cuesta la hora cara, sin exagerar."""
    if mejor <= 0 or peor <= mejor:
        return None
    r = peor / mejor
    if r >= 3:
        return "pagas el triple"
    if r >= 2:
        return "pagas más del doble"
    if r >= 1.2:
        return f"pagas un {round((r - 1) * 100)} % más"
    return None


def candidatos(fecha: date, ctx: dict) -> list[str | None]:
    """Las 8 frases del día, en orden fijo. None = no válida con los datos de hoy."""
    dia = DIAS[fecha.weekday()]
    lav, lvv = ctx["escenas"]["lavadora"], ctx["escenas"]["lavavajillas"]
    lm, lp = _num(lav["mejor_coste"]), _num(lav["peor_coste"])
    vm, vp = _num(lvv["mejor_coste"]), _num(lvv["peor_coste"])
    mas = _mas_caro(lm, lp)
    hay_dif = lp - lm >= 0.02
    pmin, pmax = _num(ctx["min"]["cifra"]), _num(ctx["max"]["cifra"])
    se_dispara = pmin > 0 and pmax / pmin >= 1.5   # en un día plano no «se dispara»
    return [
        f"No pongas la lavadora [c]{lav['peor_rango']}[/c]: {mas}." if mas else None,
        f"La luz más barata del {dia}: [b]{ctx['min']['rango']}[/b].",
        f"¿Lavadora por [b]{lav['mejor_coste']} €[/b] o por [c]{lav['peor_coste']} €[/c]? Depende de la hora." if hay_dif else None,
        f"El {dia} la luz se dispara [c]{ctx['max']['rango']}[/c]. Evítalo." if se_dispara else None,
        f"Tu lavavajillas: [b]{lvv['mejor_coste']} €[/b] o [c]{lvv['peor_coste']} €[/c]. Tú eliges la hora." if vp - vm >= 0.02 else None,
        f"De [b]{ctx['min']['cifra']}[/b] a [c]{ctx['max']['cifra']}[/c] €/kWh: así cambia la luz el {dia}.",
        f"¿A qué hora pones la lavadora el {dia}? Hay [c]{_eur(lp - lm)} €[/c] de diferencia." if hay_dif else None,
        f"El {dia}, la lavadora mejor [b]{lav['mejor_rango']}[/b]: te sale por {lav['mejor_coste']} €.",
    ]


def indice(fecha: date, ctx: dict) -> int:
    c = candidatos(fecha, ctx)
    base = fecha.toordinal() % len(c)
    for paso in range(len(c)):
        i = (base + paso) % len(c)
        if c[i]:
            return i
    raise ValueError("Ningún gancho válido")  # imposible: 3 plantillas no tienen condición


def gancho(fecha: date, ctx: dict) -> str:
    return candidatos(fecha, ctx)[indice(fecha, ctx)]


def a_html(texto: str) -> Markup:
    """Escapa el texto y convierte las marcas de color en <b class=…> (como el bocadillo)."""
    s = str(escape(texto))
    for marca, clase in (("b", "barato"), ("c", "caro")):
        s = s.replace(f"[{marca}]", f'<b class="{clase}">').replace(f"[/{marca}]", "</b>")
    return Markup(s)


def plano(texto: str) -> str:
    for m in ("[b]", "[/b]", "[c]", "[/c]"):
        texto = texto.replace(m, "")
    return texto


if __name__ == "__main__":
    from src.render import contexto

    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", type=date.fromisoformat, required=True)
    f = ap.parse_args().fecha
    ctx = contexto(f)
    elegido = indice(f, ctx)
    for i, c in enumerate(candidatos(f, ctx)):
        print(f"{'->' if i == elegido else '  '} {i + 1}. {plano(c) if c else '(no válida hoy)'}")

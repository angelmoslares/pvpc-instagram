"""
state.py — Registro de lo publicado para no publicar nunca dos veces el mismo día.

state/published.json (el carrusel sigue en el mismo formato de siempre; el reel va en su clave):
    {"2026-11-04": {"media_id": "...", "permalink": "...", "publicado_utc": "...", "fotos": 3,
                    "reel": {"contenedor": "...", "creado_utc": "...", "subido": true,
                             "media_id": "...", "permalink": "...", "publicado_utc": "...",
                             "segundos": 28, "gancho": "..."}}}

Un día sin reel, o con el reel a medias (solo «contenedor»), no cuenta como reel publicado.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUTA = ROOT / "state" / "published.json"


def ahora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cargar() -> dict:
    if not RUTA.exists():
        return {}
    return json.loads(RUTA.read_text(encoding="utf-8") or "{}")


def _guardar(datos: dict) -> None:
    RUTA.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUTA.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(datos.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(RUTA)  # escritura atómica: nunca queda un JSON a medias


def ya_publicado(fecha: date, tipo: str = "carrusel") -> dict | None:
    """Registro de lo publicado ese día, o None. tipo: "carrusel" (por defecto) o "reel"."""
    dia = cargar().get(fecha.isoformat()) or {}
    if tipo == "carrusel":
        return dia if dia.get("media_id") else None
    if tipo == "reel":
        reel = dia.get("reel") or {}
        return reel if reel.get("media_id") else None
    raise ValueError(f"Tipo desconocido: {tipo}")


def reel(fecha: date) -> dict:
    """Todo lo guardado del reel de ese día, publicado o a medias (contenedor ya creado)."""
    return dict((cargar().get(fecha.isoformat()) or {}).get("reel") or {})


def marcar(fecha: date, media_id: str, permalink: str | None, fotos: int) -> None:
    """Carrusel publicado. Conserva lo que ya hubiera del reel ese día."""
    datos = cargar()
    dia = dict(datos.get(fecha.isoformat()) or {})
    dia.update({
        "media_id": media_id,
        "permalink": permalink,
        "publicado_utc": ahora_utc(),
        "fotos": fotos,
    })
    datos[fecha.isoformat()] = dia
    _guardar(datos)


def marcar_reel(fecha: date, **campos) -> None:
    """Añade o actualiza campos del reel de ese día (contenedor, subido, media_id…)."""
    datos = cargar()
    dia = dict(datos.get(fecha.isoformat()) or {})
    dia["reel"] = {**(dia.get("reel") or {}), **campos}
    datos[fecha.isoformat()] = dia
    _guardar(datos)

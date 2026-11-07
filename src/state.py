"""
state.py — Registro de lo publicado para no publicar nunca dos veces el mismo día.

state/published.json:
    {"2026-11-04": {"media_id": "...", "permalink": "...", "publicado_utc": "...", "fotos": 3}}
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUTA = ROOT / "state" / "published.json"


def cargar() -> dict:
    if not RUTA.exists():
        return {}
    return json.loads(RUTA.read_text(encoding="utf-8") or "{}")


def ya_publicado(fecha: date) -> dict | None:
    return cargar().get(fecha.isoformat())


def marcar(fecha: date, media_id: str, permalink: str | None, fotos: int) -> None:
    datos = cargar()
    datos[fecha.isoformat()] = {
        "media_id": media_id,
        "permalink": permalink,
        "publicado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fotos": fotos,
    }
    RUTA.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUTA.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(datos.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(RUTA)  # escritura atómica: nunca queda un JSON a medias

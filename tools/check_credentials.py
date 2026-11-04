"""
check_credentials.py — Entregable de la Fase 0: comprueba que las credenciales
funcionan, sin imprimir nunca los tokens.

Uso (desde la raíz del repo, con .env relleno):
    python tools/check_credentials.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fetch_pvpc import PVPCError, cargar_env, obtener_pvpc  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

IG_BASE = "https://graph.instagram.com"


def check_esios() -> bool:
    print("\n[ESIOS]")
    if not os.getenv("ESIOS_TOKEN"):
        print("  ✗ Falta ESIOS_TOKEN en .env")
        return False
    hoy = datetime.now(ZoneInfo("Europe/Madrid")).date()
    try:
        res = obtener_pvpc(hoy, fuente="esios", guardar=False)
    except PVPCError as e:
        print(f"  ✗ {e}")
        return False
    if res.ok:
        print(f"  ✓ Token válido. PVPC de hoy ({hoy}): {len(res.horario)} horas, "
              f"media {res.horario['precio_eur_kwh'].mean():.5f} €/kWh")
    else:
        print(f"  ✓ Token aceptado, pero el día sale pendiente: {res.motivo}")
    return True


def check_instagram() -> bool:
    print("\n[Instagram]")
    token = os.getenv("IG_TOKEN", "").strip()
    user_id = os.getenv("IG_USER_ID", "").strip()
    version = os.getenv("IG_API_VERSION", "v26.0").strip()
    if not token:
        print("  ✗ Falta IG_TOKEN en .env")
        return False

    # El token va en cabecera Authorization, no en la URL, para no dejarlo en logs.
    cab = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{IG_BASE}/{version}/me",
                         params={"fields": "user_id,username,account_type"},
                         headers=cab, timeout=30)
    except requests.RequestException as e:
        print(f"  ✗ graph.instagram.com no responde: {type(e).__name__}")
        return False
    if r.status_code != 200:
        err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
        print(f"  ✗ HTTP {r.status_code}: {err.get('message', r.text[:200])}")
        return False

    me = r.json()
    print(f"  ✓ Token válido para @{me.get('username')} "
          f"(tipo {me.get('account_type', '?')}, user_id {me.get('user_id')})")

    ok = True
    if not user_id:
        print("  ✗ Falta IG_USER_ID en .env (usa el user_id de arriba)")
        ok = False
    elif str(me.get("user_id")) != user_id:
        print(f"  ✗ IG_USER_ID ({user_id}) no coincide con el user_id del token ({me.get('user_id')})")
        ok = False
    else:
        print("  ✓ IG_USER_ID coincide")

    if ok:
        r = requests.get(f"{IG_BASE}/{version}/{user_id}/content_publishing_limit",
                         params={"fields": "quota_usage,config"}, headers=cab, timeout=30)
        if r.status_code == 200 and r.json().get("data"):
            d = r.json()["data"][0]
            total = d.get("config", {}).get("quota_total", "?")
            print(f"  ✓ Permiso de publicación OK. Cuota usada: {d.get('quota_usage', '?')}/{total} en 24 h")
        else:
            print(f"  ✗ No se puede leer la cuota de publicación (HTTP {r.status_code}). "
                  "¿Falta el permiso instagram_business_content_publish?")
            ok = False
    return ok


def main() -> int:
    cargar_env()
    if not (ROOT / ".env").exists():
        print("Aviso: no hay .env en la raíz; uso solo variables de entorno del sistema.")
    resultados = [check_esios(), check_instagram()]
    print("\nTodo OK" if all(resultados) else "\nHay credenciales por resolver")
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())

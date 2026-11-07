"""
check_credentials.py — Comprueba que las credenciales funcionan, sin imprimir nunca los tokens.

Instagram admite dos tipos de token (el host se elige solo, igual que en publish.py):
    - EAA…  token de PÁGINA (API de Instagram con inicio de sesión con Facebook) → graph.facebook.com
    - IG…   token de Instagram Login → graph.instagram.com

Uso (desde la raíz del repo, con .env relleno):
    python tools/check_credentials.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fetch_pvpc import PVPCError, cargar_env, obtener_pvpc  # noqa: E402
from src.publish import API_VERSION_DEFECTO, host_y_cabecera  # noqa: E402

TZ = ZoneInfo("Europe/Madrid")
AVISO_DIAS = 21  # avisa si el acceso a datos caduca en menos de estos días


def _get(url: str, cab: dict, **params) -> tuple[int, dict]:
    try:
        r = requests.get(url, params=params, headers=cab, timeout=30)
    except requests.RequestException as e:
        return 0, {"error": {"message": f"sin respuesta ({type(e).__name__})"}}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": {"message": r.text[:200]}}


def _msg(d: dict) -> str:
    e = d.get("error", {})
    return f"{e.get('message', '?')} (code {e.get('code')}, subcode {e.get('error_subcode')})"


def _fecha(ts) -> str:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return "?"
    if ts == 0:
        return "nunca"
    return datetime.fromtimestamp(ts, TZ).strftime("%d/%m/%Y %H:%M")


def check_esios() -> bool:
    print("\n[ESIOS]")
    if not os.getenv("ESIOS_TOKEN"):
        print("  ✗ Falta ESIOS_TOKEN en .env")
        return False
    hoy = datetime.now(TZ).date()
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


def _check_facebook(base: str, cab: dict, user_id: str) -> bool:
    """Token de página (EAA…) contra graph.facebook.com."""
    ok = True
    # 1. Caducidad del propio token (debug_token admite un token de página como app de consulta).
    token = cab["Authorization"].split(" ", 1)[1]
    st, d = _get(f"{base}/debug_token", cab, input_token=token)
    if st == 200 and "data" in d:
        info = d["data"]
        tipo = info.get("type", "?")
        print(f"  ✓ Token válido: tipo {tipo}, caduca {_fecha(info.get('expires_at'))}, "
              f"acceso a datos hasta {_fecha(info.get('data_access_expires_at'))}")
        if tipo != "PAGE":
            print("  ! No es un token de PÁGINA: los de usuario caducan. Genera el de página.")
        dae = info.get("data_access_expires_at")
        if dae:
            dias = (datetime.fromtimestamp(int(dae), TZ) - datetime.now(TZ)).days
            if dias < AVISO_DIAS:
                print(f"  ! El acceso a datos caduca en {dias} días: vuelve a autorizar la app "
                      "en el Explorador de la Graph API (Generate Access Token).")
        faltan = {"instagram_basic", "instagram_content_publish", "pages_show_list"} - set(info.get("scopes", []))
        if faltan:
            print(f"  ✗ Faltan permisos: {', '.join(sorted(faltan))}")
            ok = False
    else:
        print(f"  ! No se pudo leer la caducidad (debug_token HTTP {st}); sigo con el resto.")

    # 2. La cuenta de Instagram responde con ese ID.
    if not user_id:
        print("  ✗ Falta IG_USER_ID en .env (id de instagram_business_account de la página)")
        return False
    st, d = _get(f"{base}/{user_id}", cab, fields="username,name")
    if st != 200:
        print(f"  ✗ IG_USER_ID {user_id}: HTTP {st}: {_msg(d)}")
        return False
    print(f"  ✓ IG_USER_ID corresponde a @{d.get('username')}")
    return ok


def _check_instagram(base: str, cab: dict, user_id: str) -> bool:
    """Token de Instagram Login (IG…) contra graph.instagram.com."""
    st, me = _get(f"{base}/me", cab, fields="user_id,username,account_type")
    if st != 200:
        print(f"  ✗ HTTP {st}: {_msg(me)}")
        return False
    print(f"  ✓ Token válido para @{me.get('username')} "
          f"(tipo {me.get('account_type', '?')}, user_id {me.get('user_id')})")
    if not user_id:
        print("  ✗ Falta IG_USER_ID en .env (usa el user_id de arriba)")
        return False
    if str(me.get("user_id")) != user_id:
        print(f"  ✗ IG_USER_ID ({user_id}) no coincide con el user_id del token ({me.get('user_id')})")
        return False
    print("  ✓ IG_USER_ID coincide")
    return True


def check_instagram() -> bool:
    print("\n[Instagram]")
    token = os.getenv("IG_TOKEN", "").strip()
    user_id = os.getenv("IG_USER_ID", "").strip()
    version = os.getenv("IG_API_VERSION", API_VERSION_DEFECTO).strip()
    if not token:
        print("  ✗ Falta IG_TOKEN en .env")
        return False

    host, cab = host_y_cabecera(token)
    base = f"https://{host}/{version}"
    print(f"  · Host {host}, versión {version}")

    ok = _check_facebook(base, cab, user_id) if host == "graph.facebook.com" \
        else _check_instagram(base, cab, user_id)

    if ok:
        st, d = _get(f"{base}/{user_id}/content_publishing_limit", cab, fields="quota_usage,config")
        if st == 200 and d.get("data"):
            q = d["data"][0]
            print(f"  ✓ Permiso de publicación OK. Cuota usada: {q.get('quota_usage', '?')}/"
                  f"{q.get('config', {}).get('quota_total', '?')} en 24 h")
        else:
            print(f"  ✗ No se puede leer la cuota de publicación: HTTP {st}: {_msg(d)}")
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

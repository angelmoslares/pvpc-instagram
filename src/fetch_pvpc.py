"""
fetch_pvpc.py — Descarga y valida el PVPC 2.0TD (término de energía, sin impuestos)
de un día desde ESIOS (indicador 1001, Península).

- Por defecto pide D+1 en hora de Madrid.
- Acepta datos cuartohorarios (lo normal desde oct-2025) u horarios, y devuelve
  siempre la media horaria + (si existe) el detalle cuartohorario.
- Agrupa por hora UTC, no por hora local: así el día de 25 h no mezcla las dos
  "02:00" del cambio de hora de octubre.
- Datos ausentes o incompletos -> estado "pendiente" (no es un error).
- Problemas reales (token, red, geo_id, valores absurdos) -> PVPCError.

Uso en local (desde la raíz del repo):
    python -m src.fetch_pvpc                      # D+1
    python -m src.fetch_pvpc --fecha 2026-10-25   # un día concreto (25 h)
    python -m src.fetch_pvpc --fuente redata      # probar el plan B
    python -m src.fetch_pvpc --no-guardar         # no escribir CSV

Códigos de salida: 0 = ok, 2 = pendiente, 1 = error.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
TZ = ZoneInfo("Europe/Madrid")
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

ESIOS_URL = "https://api.esios.ree.es/indicators/1001"
REDATA_URL = "https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
GEO_PENINSULA = 8741

# Rango de cordura para el término de energía del PVPC (€/kWh, sin impuestos).
# Fuera de aquí no publicamos: casi seguro es un dato mal leído.
PRECIO_MIN_KWH = -0.10
PRECIO_MAX_KWH = 1.00

TIMEOUT_S = 30
USER_AGENT = "pvpc-instagram/1.0"

log = logging.getLogger("fetch_pvpc")


class PVPCError(Exception):
    """Error real (no 'pendiente'): credenciales, red, formato o dato absurdo."""


@dataclass
class ResultadoPVPC:
    estado: str                                  # "ok" | "pendiente"
    fecha: date
    fuente: str | None = None                    # "esios" | "redata"
    horario: pd.DataFrame | None = None          # 23/24/25 filas
    cuartohorario: pd.DataFrame | None = None    # 92/96/100 filas o None
    motivo: str = ""

    @property
    def ok(self) -> bool:
        return self.estado == "ok"


# --------------------------------------------------------------------------- #
# Utilidades de fecha
# --------------------------------------------------------------------------- #
def manana_madrid(ahora: datetime | None = None) -> date:
    """Fecha de D+1 en hora de Madrid."""
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(TZ)
    return ahora.date() + timedelta(days=1)


def limites_utc(fecha: date) -> tuple[datetime, datetime]:
    """[00:00 del día, 00:00 del día siguiente) en hora de Madrid, expresado en UTC."""
    inicio = datetime.combine(fecha, time(0), tzinfo=TZ)
    fin = datetime.combine(fecha + timedelta(days=1), time(0), tzinfo=TZ)
    return inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc)


def horas_del_dia(fecha: date) -> int:
    """24 normalmente; 23 el último domingo de marzo; 25 el último de octubre."""
    inicio, fin = limites_utc(fecha)
    return int((fin - inicio).total_seconds() // 3600)


def cargar_env() -> None:
    """Carga .env en local si existe. En GitHub Actions las variables vienen de Secrets."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


# --------------------------------------------------------------------------- #
# Descarga: ESIOS
# --------------------------------------------------------------------------- #
def _descargar_esios(fecha: date, token: str) -> pd.DataFrame:
    """Devuelve DataFrame crudo [inicio_utc, precio_eur_mwh] filtrado a Península."""
    if not token:
        raise PVPCError("Falta ESIOS_TOKEN (en .env o en GitHub Secrets).")

    inicio, fin = limites_utc(fecha)
    params = {
        "start_date": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date": (fin - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "geo_ids[]": GEO_PENINSULA,
    }
    headers = {
        "Accept": "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "x-api-key": token,
        "User-Agent": USER_AGENT,
    }

    try:
        r = requests.get(ESIOS_URL, params=params, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        # El mensaje de requests incluye la URL, pero el token va en cabecera: no se filtra.
        raise PVPCError(f"ESIOS no responde: {type(e).__name__}") from e

    if r.status_code in (401, 403):
        raise PVPCError(f"ESIOS rechaza el token (HTTP {r.status_code}).")
    if r.status_code != 200:
        raise PVPCError(f"ESIOS devuelve HTTP {r.status_code}: {r.text[:200]}")

    try:
        valores = r.json().get("indicator", {}).get("values", [])
    except ValueError as e:
        raise PVPCError("ESIOS devuelve algo que no es JSON.") from e

    df = pd.DataFrame(valores)
    if df.empty:
        return pd.DataFrame(columns=["inicio_utc", "precio_eur_mwh"])

    if "geo_id" in df.columns:
        peninsula = df[df["geo_id"] == GEO_PENINSULA]
        if peninsula.empty:
            geos = df[["geo_id", "geo_name"]].drop_duplicates().to_dict("records") \
                if "geo_name" in df.columns else df["geo_id"].unique().tolist()
            raise PVPCError(f"No hay valores con geo_id {GEO_PENINSULA}. Geos recibidas: {geos}")
        df = peninsula

    col_fecha = "datetime_utc" if "datetime_utc" in df.columns else "datetime"
    return pd.DataFrame({
        "inicio_utc": pd.to_datetime(df[col_fecha], utc=True),
        "precio_eur_mwh": pd.to_numeric(df["value"], errors="coerce"),
    })


# --------------------------------------------------------------------------- #
# Descarga: REData (plan B, sin token)
# --------------------------------------------------------------------------- #
def _descargar_redata(fecha: date) -> pd.DataFrame:
    """
    Plan B sin autenticación. NO VERIFICADO contra la API real: compáralo con
    ESIOS con --fuente redata antes de confiar en él.
    """
    inicio_local = datetime.combine(fecha, time(0))
    fin_local = datetime.combine(fecha, time(23, 59))
    params = {
        "start_date": inicio_local.strftime("%Y-%m-%dT%H:%M"),
        "end_date": fin_local.strftime("%Y-%m-%dT%H:%M"),
        "time_trunc": "hour",
        "geo_limit": "peninsular",
        "geo_ids": GEO_PENINSULA,
    }
    try:
        r = requests.get(REDATA_URL, params=params,
                         headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                         timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise PVPCError(f"REData no responde: {type(e).__name__}") from e
    if r.status_code != 200:
        raise PVPCError(f"REData devuelve HTTP {r.status_code}: {r.text[:200]}")

    incluidos = r.json().get("included", [])
    serie = next(
        (i for i in incluidos
         if str(i.get("id")) == "1001"
         or "PVPC" in str(i.get("attributes", {}).get("title", "")).upper()),
        None,
    )
    if serie is None:
        titulos = [i.get("attributes", {}).get("title") for i in incluidos]
        raise PVPCError(f"REData no trae la serie PVPC. Series recibidas: {titulos}")

    df = pd.DataFrame(serie.get("attributes", {}).get("values", []))
    if df.empty:
        return pd.DataFrame(columns=["inicio_utc", "precio_eur_mwh"])
    return pd.DataFrame({
        "inicio_utc": pd.to_datetime(df["datetime"], utc=True),
        "precio_eur_mwh": pd.to_numeric(df["value"], errors="coerce"),
    })


# --------------------------------------------------------------------------- #
# Validación y agregación
# --------------------------------------------------------------------------- #
def _normalizar(crudo: pd.DataFrame, fecha: date, fuente: str) -> ResultadoPVPC:
    inicio, fin = limites_utc(fecha)
    n_horas = horas_del_dia(fecha)

    df = (crudo[(crudo["inicio_utc"] >= inicio) & (crudo["inicio_utc"] < fin)]
          .drop_duplicates("inicio_utc")
          .sort_values("inicio_utc")
          .reset_index(drop=True))
    n = len(df)

    if n == 0:
        return ResultadoPVPC("pendiente", fecha, fuente, motivo="Sin datos publicados todavía.")

    # Granularidad: cuartohoraria (4·h valores) u horaria (h valores)
    if n == 4 * n_horas:
        paso, cuarto = pd.Timedelta(minutes=15), True
    elif n == n_horas:
        paso, cuarto = pd.Timedelta(hours=1), False
    elif n < 4 * n_horas:
        return ResultadoPVPC("pendiente", fecha, fuente,
                             motivo=f"Datos incompletos: {n} valores "
                                    f"(esperados {n_horas} horarios o {4 * n_horas} cuartohorarios).")
    else:
        raise PVPCError(f"Demasiados valores: {n} para un día de {n_horas} h.")

    saltos = df["inicio_utc"].diff().dropna()
    if not (saltos == paso).all():
        return ResultadoPVPC("pendiente", fecha, fuente,
                             motivo=f"Huecos en la serie (paso esperado {paso}).")

    if df["precio_eur_mwh"].isna().any():
        return ResultadoPVPC("pendiente", fecha, fuente,
                             motivo=f"{int(df['precio_eur_mwh'].isna().sum())} valores nulos.")

    df["precio_eur_kwh"] = df["precio_eur_mwh"] / 1000.0
    fuera = df[(df["precio_eur_kwh"] < PRECIO_MIN_KWH) | (df["precio_eur_kwh"] > PRECIO_MAX_KWH)]
    if not fuera.empty:
        raise PVPCError(f"{len(fuera)} valores fuera de rango "
                        f"[{PRECIO_MIN_KWH}, {PRECIO_MAX_KWH}] €/kWh, p. ej. "
                        f"{fuera['precio_eur_kwh'].iloc[0]:.5f}. No se publica.")

    # Media horaria agrupando por hora UTC (seguro en días de 25 h)
    df["hora_utc"] = df["inicio_utc"].dt.floor("h")
    horario = (df.groupby("hora_utc", as_index=False)
                 .agg(precio_eur_kwh=("precio_eur_kwh", "mean"),
                      n_cuartos=("precio_eur_kwh", "size")))
    if len(horario) != n_horas:
        raise PVPCError(f"La agregación da {len(horario)} horas, esperadas {n_horas}.")
    if cuarto and not (horario["n_cuartos"] == 4).all():
        raise PVPCError("Alguna hora no tiene exactamente 4 cuartos.")

    horario["hora_local"] = horario["hora_utc"].dt.tz_convert(TZ)
    horario["hora"] = horario["hora_local"].dt.hour
    # Etiqueta legible; la hora repetida del día de 25 h se marca con ' (02 y 02'), porque las fuentes pixel no tienen el signo ′
    repetida = horario.duplicated("hora", keep="first")
    horario["etiqueta"] = horario["hora"].map("{:02d}".format) + repetida.map({True: "'", False: ""})
    horario["precio_eur_kwh"] = horario["precio_eur_kwh"].round(6)
    horario = horario[["hora_local", "hora_utc", "hora", "etiqueta", "precio_eur_kwh"]]

    qh = None
    if cuarto:
        qh = pd.DataFrame({
            "inicio_local": df["inicio_utc"].dt.tz_convert(TZ),
            "inicio_utc": df["inicio_utc"],
            "precio_eur_kwh": df["precio_eur_kwh"].round(6),
        })

    return ResultadoPVPC("ok", fecha, fuente, horario=horario, cuartohorario=qh)


# --------------------------------------------------------------------------- #
# API pública del módulo
# --------------------------------------------------------------------------- #
def obtener_pvpc(fecha: date | None = None, fuente: str = "auto",
                 guardar: bool = True) -> ResultadoPVPC:
    """
    fuente: "esios" | "redata" | "auto" (ESIOS y, solo si ESIOS da error, REData).
    Si ESIOS dice "pendiente" no se consulta REData: tendría el mismo retraso.
    """
    cargar_env()
    fecha = fecha or manana_madrid()

    if fuente in ("esios", "auto"):
        try:
            res = _normalizar(_descargar_esios(fecha, os.getenv("ESIOS_TOKEN", "").strip()),
                              fecha, "esios")
        except PVPCError as e:
            if fuente == "esios":
                raise
            log.warning("ESIOS falla (%s). Probando REData.", e)
            res = _normalizar(_descargar_redata(fecha), fecha, "redata")
    elif fuente == "redata":
        res = _normalizar(_descargar_redata(fecha), fecha, "redata")
    else:
        raise ValueError(f"Fuente desconocida: {fuente}")

    if res.ok and guardar:
        guardar_csv(res)
    return res


def guardar_csv(res: ResultadoPVPC) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ruta = DATA_DIR / f"pvpc_{res.fecha.isoformat()}.csv"
    res.horario.to_csv(ruta, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
    if res.cuartohorario is not None:
        res.cuartohorario.to_csv(DATA_DIR / f"pvpc_{res.fecha.isoformat()}_qh.csv",
                                 index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
    return ruta


# --------------------------------------------------------------------------- #
# CLI para pruebas en local
# --------------------------------------------------------------------------- #
def _main() -> int:
    p = argparse.ArgumentParser(description="Descarga y valida el PVPC de un día.")
    p.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto D+1 Madrid)")
    p.add_argument("--fuente", choices=["auto", "esios", "redata"], default="auto")
    p.add_argument("--no-guardar", action="store_true", help="No escribir CSV en data/")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        res = obtener_pvpc(a.fecha, a.fuente, guardar=not a.no_guardar)
    except PVPCError as e:
        log.error("ERROR: %s", e)
        return 1

    if not res.ok:
        log.info("PENDIENTE %s (%s): %s", res.fecha, res.fuente, res.motivo)
        return 2

    h = res.horario
    pd.set_option("display.width", 120)
    print(h[["etiqueta", "hora_local", "precio_eur_kwh"]].to_string(index=False))
    i_min, i_max = h["precio_eur_kwh"].idxmin(), h["precio_eur_kwh"].idxmax()
    print(f"\nFecha {res.fecha} · fuente {res.fuente} · {len(h)} h · "
          f"{'cuartohorario' if res.cuartohorario is not None else 'horario'}")
    print(f"Media {h['precio_eur_kwh'].mean():.5f} €/kWh · "
          f"mín {h.loc[i_min, 'precio_eur_kwh']:.5f} ({h.loc[i_min, 'etiqueta']} h) · "
          f"máx {h.loc[i_max, 'precio_eur_kwh']:.5f} ({h.loc[i_max, 'etiqueta']} h)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())

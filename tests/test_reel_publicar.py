"""
Pruebas de la publicación del reel con una API de Meta simulada (no se conecta a nada).

Necesitan out/2026-11-10_reel.mp4 y data/pvpc_2026-11-10.csv:
    python -m src.render_reel --fecha 2026-11-10
    python -m unittest tests.test_reel_publicar -v
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src import publish, state

FECHA = date(2026, 11, 10)
ROOT = Path(__file__).resolve().parents[1]
MP4 = ROOT / "out" / f"{FECHA.isoformat()}_reel.mp4"


class Resp:
    def __init__(self, datos: dict, status: int = 200):
        self.status_code, self._datos = status, datos
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


class MetaFalsa:
    """Imita lo justo de graph.facebook.com y rupload.facebook.com y apunta cada llamada."""

    def __init__(self, estado_contenedor="FINISHED", recientes=None, subida_ok=True):
        self.llamadas: list[tuple] = []
        self.estado_contenedor = estado_contenedor
        self.recientes = recientes or []
        self.subida_ok = subida_ok
        self.headers: dict = {}
        self.cuerpo_subido = None

    # requests.Session
    def request(self, metodo, url, params=None, data=None, timeout=None):
        ruta = url.split("/v27.0/", 1)[1]
        self.llamadas.append((metodo, ruta, dict(params or data or {})))
        if ruta.endswith("/content_publishing_limit"):
            return Resp({"data": [{"quota_usage": 1, "config": {"quota_total": 100}}]})
        if metodo == "GET" and ruta == "123/media":
            return Resp({"data": self.recientes})
        if metodo == "POST" and ruta == "123/media":
            return Resp({"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/C1"})
        if metodo == "GET" and ruta in ("C1", "C0"):
            return Resp({"status_code": self.estado_contenedor, "status": "x"})
        if metodo == "POST" and ruta == "123/media_publish":
            return Resp({"id": "M1"})
        if metodo == "GET" and ruta == "M1":
            return Resp({"permalink": "https://www.instagram.com/reel/abc/"})
        raise AssertionError(f"Llamada inesperada {metodo} {ruta}")

    def post(self, url, data=None, timeout=None, headers=None):
        self.llamadas.append(("UPLOAD", url, dict(headers or {})))
        self.cuerpo_subido = data
        return Resp({"success": True} if self.subida_ok else {"debug_info": {"type": "ProcessingFailedError"}},
                    200 if self.subida_ok else 400)

    def rutas(self, metodo=None):
        return [r for m, r, _ in self.llamadas if metodo is None or m == metodo]


class Base(unittest.TestCase):
    def setUp(self):
        if not MP4.exists():
            self.skipTest(f"Falta {MP4.name}: python -m src.render_reel --fecha {FECHA}")
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "out").mkdir()
        shutil.copy2(MP4, self.tmp / "out" / MP4.name)
        self.ruta_estado = self.tmp / "published.json"
        self.parches = [
            mock.patch.object(state, "RUTA", self.ruta_estado),
            mock.patch.object(publish, "OUT_DIR", self.tmp / "out"),
            mock.patch.object(publish.time, "sleep", lambda s: None),
            mock.patch.object(publish, "cargar_env", lambda: None),
            mock.patch.dict(os.environ, {"IG_TOKEN": "EAAfalso", "IG_USER_ID": "123",
                                         "IG_API_VERSION": "v27.0"}, clear=False),
        ]
        for p in self.parches:
            p.start()
        os.environ.pop("IG_GRAPH_HOST", None)

    def tearDown(self):
        for p in reversed(self.parches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def con_meta(self, meta: MetaFalsa):
        return mock.patch.object(publish.requests, "Session", lambda: meta)

    def estado(self) -> dict:
        return json.loads(self.ruta_estado.read_text(encoding="utf-8")) if self.ruta_estado.exists() else {}


class PublicarReel(Base):
    def test_flujo_completo(self):
        # El carrusel ya estaba publicado: el registro del reel se añade sin tocarlo
        state.marcar(FECHA, "CARR", "https://www.instagram.com/p/carr/", 3)
        meta = MetaFalsa()
        with self.con_meta(meta):
            enlace = publish.publicar_reel(FECHA)
        self.assertEqual(enlace, "https://www.instagram.com/reel/abc/")

        crear = [d for m, r, d in meta.llamadas if m == "POST" and r == "123/media"][0]
        self.assertEqual(crear["media_type"], "REELS")
        self.assertEqual(crear["upload_type"], "resumable")
        self.assertEqual(crear["thumb_offset"], "6500")
        self.assertEqual(crear["share_to_feed"], "true")
        self.assertTrue(crear["caption"].startswith("💡 Precio de la luz del martes 10 de noviembre"))
        self.assertIn("Envíaselo a esa persona que necesita ahorrar", crear["caption"])

        subida = [(u, h) for m, u, h in meta.llamadas if m == "UPLOAD"][0]
        self.assertEqual(subida[0], "https://rupload.facebook.com/ig-api-upload/v27.0/C1")
        self.assertEqual(subida[1]["offset"], "0")
        self.assertEqual(int(subida[1]["file_size"]), MP4.stat().st_size)
        self.assertEqual(meta.cuerpo_subido, MP4.read_bytes())
        self.assertEqual(meta.headers["Authorization"], "OAuth EAAfalso")
        self.assertNotIn("EAAfalso", subida[0])  # el token nunca va en la URL

        dia = self.estado()[FECHA.isoformat()]
        self.assertEqual(dia["media_id"], "CARR")  # carrusel intacto
        self.assertEqual(dia["reel"]["media_id"], "M1")
        self.assertTrue(dia["reel"]["subido"])
        self.assertEqual(dia["reel"]["segundos"], 28)
        self.assertTrue(dia["reel"]["gancho"])
        self.assertEqual(state.ya_publicado(FECHA, "reel")["permalink"], "https://www.instagram.com/reel/abc/")
        self.assertEqual(state.ya_publicado(FECHA)["media_id"], "CARR")

    def test_prueba_no_publica_ni_toca_el_estado(self):
        meta = MetaFalsa()
        with self.con_meta(meta):
            self.assertIsNone(publish.publicar_reel(FECHA, prueba=True))
        self.assertNotIn("123/media_publish", meta.rutas())
        self.assertIn("UPLOAD", [m for m, _, _ in meta.llamadas])
        self.assertFalse(self.ruta_estado.exists())

    def test_ya_publicado_no_llama_a_meta(self):
        state.marcar_reel(FECHA, media_id="M0", permalink="https://www.instagram.com/reel/viejo/")
        meta = MetaFalsa()
        with self.con_meta(meta):
            self.assertEqual(publish.publicar_reel(FECHA), "https://www.instagram.com/reel/viejo/")
        self.assertEqual(meta.llamadas, [])

    def test_reutiliza_contenedor_subido(self):
        hace_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        state.marcar_reel(FECHA, contenedor="C0", creado_utc=hace_1h, subido=True)
        meta = MetaFalsa()
        with self.con_meta(meta):
            publish.publicar_reel(FECHA)
        self.assertNotIn(("POST", "123/media"), [(m, r) for m, r, _ in meta.llamadas])
        self.assertNotIn("UPLOAD", [m for m, _, _ in meta.llamadas])
        publicar = [d for m, r, d in meta.llamadas if r == "123/media_publish"][0]
        self.assertEqual(publicar["creation_id"], "C0")

    def test_no_reutiliza_contenedor_caducado_o_sin_subir(self):
        hace_30h = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(timespec="seconds")
        for previo in ({"contenedor": "C0", "creado_utc": hace_30h, "subido": True},
                       {"contenedor": "C0", "creado_utc": state.ahora_utc(), "subido": False}):
            self.ruta_estado.unlink(missing_ok=True)
            state.marcar_reel(FECHA, **previo)
            meta = MetaFalsa()
            with self.con_meta(meta):
                publish.publicar_reel(FECHA)
            self.assertIn("UPLOAD", [m for m, _, _ in meta.llamadas], previo)

    def test_anti_duplicado(self):
        pie = publish.pie_de_foto(FECHA, "reel")
        meta = MetaFalsa(recientes=[
            {"id": "X", "media_product_type": "FEED", "caption": pie.splitlines()[0]},  # el carrusel
            {"id": "R9", "media_product_type": "REELS", "caption": pie, "permalink": "https://www.instagram.com/reel/r9/"},
        ])
        with self.con_meta(meta):
            self.assertEqual(publish.publicar_reel(FECHA), "https://www.instagram.com/reel/r9/")
        self.assertNotIn("UPLOAD", [m for m, _, _ in meta.llamadas])
        self.assertNotIn("123/media_publish", meta.rutas())
        self.assertEqual(state.ya_publicado(FECHA, "reel")["media_id"], "R9")

    def test_contenedor_en_error(self):
        meta = MetaFalsa(estado_contenedor="ERROR")
        with self.con_meta(meta), self.assertRaises(publish.PublishError):
            publish.publicar_reel(FECHA)
        self.assertNotIn("123/media_publish", meta.rutas())

    def test_subida_fallida_deja_el_contenedor_sin_marcar_como_subido(self):
        meta = MetaFalsa(subida_ok=False)
        with self.con_meta(meta), self.assertRaises(publish.PublishError):
            publish.publicar_reel(FECHA)
        self.assertFalse(state.reel(FECHA)["subido"])
        self.assertIsNone(state.ya_publicado(FECHA, "reel"))

    def test_token_ig_no_admite_subida_resumable(self):
        with mock.patch.dict(os.environ, {"IG_TOKEN": "IGfalso"}), self.con_meta(MetaFalsa()), \
                self.assertRaises(publish.PublishError):
            publish.publicar_reel(FECHA)

    def test_mp4_que_no_cumple(self):
        (self.tmp / "out" / MP4.name).write_bytes(b"no es un mp4")
        with self.con_meta(MetaFalsa()), self.assertRaises(publish.PublishError):
            publish.publicar_reel(FECHA)


class Estado(Base):
    def test_formato_antiguo_sigue_valiendo(self):
        self.ruta_estado.write_text(json.dumps({"2026-11-09": {"media_id": "A", "permalink": "p", "fotos": 3}}),
                                    encoding="utf-8")
        self.assertEqual(state.ya_publicado(date(2026, 11, 9))["media_id"], "A")
        self.assertIsNone(state.ya_publicado(date(2026, 11, 9), "reel"))

    def test_reel_a_medias_no_cuenta_como_publicado(self):
        state.marcar_reel(FECHA, contenedor="C1", subido=True)
        self.assertIsNone(state.ya_publicado(FECHA, "reel"))
        self.assertIsNone(state.ya_publicado(FECHA))

    def test_marcar_carrusel_conserva_el_reel(self):
        state.marcar_reel(FECHA, contenedor="C1")
        state.marcar(FECHA, "CARR", None, 3)
        self.assertEqual(state.reel(FECHA)["contenedor"], "C1")


class Orquestacion(Base):
    def setUp(self):
        super().setUp()
        import main
        p = mock.patch.object(main, "sincronizar", lambda: None)  # nunca un git pull de verdad
        p.start()
        self.addCleanup(p.stop)

    def test_carrusel_hecho_solo_reintenta_el_reel(self):
        import main
        state.marcar(FECHA, "CARR", "p", 3)
        with mock.patch.object(main, "fase_carrusel") as carr, mock.patch.object(main, "fase_reel", return_value=0) as reel:
            self.assertEqual(main.ejecutar(FECHA, push=True), 0)
        carr.assert_not_called()
        reel.assert_called_once()

    def test_todo_hecho_no_hace_nada(self):
        import main
        state.marcar(FECHA, "CARR", "p", 3)
        state.marcar_reel(FECHA, media_id="M1", permalink="r")
        with mock.patch.object(main, "fase_carrusel") as carr, mock.patch.object(main, "fase_reel") as reel:
            self.assertEqual(main.ejecutar(FECHA, push=True), 0)
        carr.assert_not_called()
        reel.assert_not_called()

    def test_fallo_del_reel_no_afecta_al_carrusel(self):
        import main
        with mock.patch.object(main, "fase_carrusel", return_value=0) as carr, \
                mock.patch.object(main, "fase_reel", side_effect=RuntimeError("HyperFrames")):
            self.assertEqual(main.ejecutar(FECHA, push=True), 1)
        carr.assert_called_once()

    def test_pendiente_no_intenta_el_reel(self):
        import main
        with mock.patch.object(main, "fase_carrusel", return_value=2), mock.patch.object(main, "fase_reel") as reel:
            self.assertEqual(main.ejecutar(FECHA, push=True), 2)
        reel.assert_not_called()

    def test_sin_reel(self):
        import main
        with mock.patch.object(main, "fase_carrusel", return_value=0), mock.patch.object(main, "fase_reel") as reel:
            self.assertEqual(main.ejecutar(FECHA, push=True, con_reel=False), 0)
        reel.assert_not_called()

    def test_fase_reel_sube_el_estado_aunque_falle_la_publicacion(self):
        import main
        with mock.patch("src.render_reel.render_reel", return_value=ROOT / "out" / MP4.name), \
                mock.patch.object(main, "publicar_reel", side_effect=publish.PublishError("Meta")), \
                mock.patch.object(main, "subir") as subir, self.assertRaises(publish.PublishError):
            main.fase_reel(FECHA, sin_publicar=False, prueba=False, forzar=False, push=True)
        subir.assert_called_once_with(["state"], f"Reel del {FECHA}")



class Git(Base):
    """main.py con git simulado: nunca toca el repo de verdad."""

    def git_falso(self, fallos_push=0):
        llamadas, estado = [], {"push": 0}

        def _git(*args):
            llamadas.append(args)
            rc = 0
            if args[0] == "diff":
                rc = 1  # hay cambios que subir
            if args[0] == "push":
                estado["push"] += 1
                rc = 1 if estado["push"] <= fallos_push else 0
            return mock.Mock(returncode=rc, stderr="! [rejected] HEAD -> main (fetch first)" if rc else "", stdout="")
        return _git, llamadas

    def test_con_push_se_pone_al_dia_antes_de_leer_el_estado(self):
        import main
        _git, llamadas = self.git_falso()

        def pull_que_trae_el_carrusel(*args):
            r = _git(*args)
            if args[0] == "pull":  # GitHub ya tenía el carrusel publicado por el primer intento
                state.marcar(FECHA, "CARR", "p", 3)
            return r
        with mock.patch.object(main, "_git", pull_que_trae_el_carrusel), \
                mock.patch.object(main, "fase_carrusel") as carr, mock.patch.object(main, "fase_reel", return_value=0):
            main.ejecutar(FECHA, push=True)
        self.assertEqual(llamadas[0][0], "pull")
        carr.assert_not_called()  # no se repite el carrusel

    def test_push_rechazado_se_rebasa_y_reintenta(self):
        import main
        _git, llamadas = self.git_falso(fallos_push=1)
        with mock.patch.object(main, "_git", _git):
            self.assertTrue(main.subir(["state"], "Reel"))
        ordenes = [a[0] for a in llamadas]
        self.assertEqual(ordenes.count("push"), 2)
        self.assertIn("pull", ordenes[ordenes.index("push"):])

    def test_push_rechazado_dos_veces_es_error(self):
        import main
        _git, _ = self.git_falso(fallos_push=2)
        with mock.patch.object(main, "_git", _git), self.assertRaises(RuntimeError):
            main.subir(["state"], "Reel")


class FechaObjetivo(Base):
    """Disparos retrasados por GitHub: de madrugada se remata el día de hoy, no se salta a D+1."""

    MADRID_0035 = datetime(2026, 11, 10, 23, 35, tzinfo=timezone.utc)   # 00:35 del 11/11 en Madrid
    MADRID_0335 = datetime(2026, 11, 10, 2, 35, tzinfo=timezone.utc)    # 03:35 del 10/11
    MADRID_2035 = datetime(2026, 11, 10, 19, 35, tzinfo=timezone.utc)   # 20:35 del 10/11
    MADRID_1300 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)    # 13:00 del 10/11

    def test_tarde_normal_es_d_mas_1(self):
        import main
        self.assertEqual(main.fecha_objetivo(self.MADRID_2035), date(2026, 11, 11))

    def test_madrugada_sin_publicar_es_hoy(self):
        import main
        self.assertEqual(main.fecha_objetivo(self.MADRID_0335), date(2026, 11, 10))
        self.assertEqual(main.fecha_objetivo(self.MADRID_0035), date(2026, 11, 11))

    def test_madrugada_con_carrusel_pero_sin_reel_es_hoy(self):
        import main
        state.marcar(date(2026, 11, 10), "c1", None, 3)
        self.assertEqual(main.fecha_objetivo(self.MADRID_0335), date(2026, 11, 10))
        self.assertEqual(main.fecha_objetivo(self.MADRID_0335, con_reel=False), date(2026, 11, 11))

    def test_madrugada_todo_publicado_es_d_mas_1(self):
        import main
        state.marcar(date(2026, 11, 10), "c1", None, 3)
        state.marcar_reel(date(2026, 11, 10), media_id="r1")
        self.assertEqual(main.fecha_objetivo(self.MADRID_0335), date(2026, 11, 11))

    def test_pasado_el_mediodia_no_rescata(self):
        import main
        self.assertEqual(main.fecha_objetivo(self.MADRID_1300), date(2026, 11, 11))

    def test_cambio_de_hora_25_horas(self):
        import main
        # 25/10/2026: 01:30 UTC = 02:30 Madrid (ya en invierno) → hoy si no está publicado
        self.assertEqual(main.fecha_objetivo(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)),
                         date(2026, 10, 25))


if __name__ == "__main__":
    unittest.main()

"""La pantalla de archivos agrupa por el mes de los movimientos, no por el de subida."""
from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.files import _agrupar_por_mes, _titulo_mes

from tests.conftest import reset_db

JUNIO = "fecha,concepto,importe\n2026-06-03,NETFLIX,12990\n2026-06-10,COTO,48200\n"
MAYO = "fecha,concepto,importe\n2026-05-02,YPF,35000\n2026-05-20,FARMACITY,18300\n"
# Un resumen de tarjeta que cierra a mitad de mes: mas filas de abril.
CIERRE = (
    "fecha,concepto,importe\n"
    "2026-04-20,A,1\n2026-04-25,B,1\n2026-04-28,C,1\n2026-05-02,D,1\n"
)
SIN_FECHA = "concepto,importe\nALGO,10\n"


@pytest.fixture
def auth():
    reset_db()
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        yield client


def _subir(client, nombre, contenido, revisar=""):
    return client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(contenido.encode()), "text/csv")},
        data={"revisar": revisar},
        follow_redirects=False,
    )


def _secciones(html: str) -> list[str]:
    return re.findall(r'<section class="file-month" data-mes="([^"]+)"', html)


def test_el_mes_sale_de_los_movimientos_y_el_ultimo_va_arriba(auth):
    # Junio se sube antes que mayo: por fecha de subida mayo iria arriba.
    _subir(auth, "junio.csv", JUNIO)
    _subir(auth, "mayo.csv", MAYO)
    html = auth.get("/archivos").text
    assert _secciones(html) == ["2026-06", "2026-05"]
    assert html.index("Junio 2026") < html.index("Mayo 2026")
    # Cada archivo esta dentro de su mes, no del mes en que se subio.
    assert html.index("Junio 2026") < html.index("junio.csv") < html.index("Mayo 2026")
    assert html.index("Mayo 2026") < html.index("mayo.csv")


def test_archivo_a_caballo_entre_dos_meses_va_al_mes_con_mas_movimientos(auth):
    _subir(auth, "cierre.csv", CIERRE)
    html = auth.get("/archivos").text
    assert _secciones(html) == ["2026-04"]


def test_sin_importar_arriba_y_sin_fecha_abajo(auth):
    _subir(auth, "mayo.csv", MAYO)
    _subir(auth, "pendiente.csv", JUNIO, revisar="1")
    _subir(auth, "sinfecha.csv", SIN_FECHA)
    html = auth.get("/archivos").text
    assert _secciones(html) == ["sin-importar", "2026-05", "sin-fecha"]


def test_titulos_y_agrupado():
    assert _titulo_mes("2026-01") == "Enero 2026"
    assert _titulo_mes("2025-12") == "Diciembre 2025"

    class Obj:
        def __init__(self, imported):
            self.imported = imported

    archivos = [
        {"obj": Obj(True), "mes": "2026-05"},
        {"obj": Obj(True), "mes": "2026-07"},
        {"obj": Obj(True), "mes": "2026-05"},
        {"obj": Obj(False), "mes": ""},
    ]
    secciones = _agrupar_por_mes(archivos)
    assert [s["clave"] for s in secciones] == ["sin-importar", "2026-07", "2026-05"]
    assert len(secciones[2]["files"]) == 2

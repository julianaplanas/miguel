"""Revisar lo que se leyo de un archivo antes de que entre en los totales."""
from __future__ import annotations

import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Transaction, UploadedFile

from tests.conftest import reset_db

CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,YPF SERVICIOS SRL,Ana,35000.00\n"
    "2026-03-03,ARRASTRE EJERCICIO ANTERIOR,Ana,1250000.00\n"
)


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


def _subir(client, nombre="marzo.csv", csv=CSV, revisar="1"):
    datos = {"default_person": "", "default_currency": "ARS"}
    if revisar:
        datos["revisar"] = revisar
    return client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data=datos,
        follow_redirects=False,
    )


def _file_id(client) -> int:
    with SessionLocal() as db:
        return db.execute(select(UploadedFile)).scalars().first().id


def _importar(client, file_id, excluir=(), **extra):
    datos = {
        "accion": "importar",
        "date": "fecha",
        "amount": "importe",
        "description": "concepto",
        "person": "persona",
        "default_person": "",
        "default_currency": "ARS",
        "invert_sign": "",
    }
    datos.update(extra)
    # httpx repite una clave cuando el valor es una lista; no acepta una
    # lista de tuplas (la toma como cuerpo crudo y manda cualquier cosa).
    if excluir:
        datos["excluir"] = [str(i) for i in excluir]
    return client.post(f"/archivos/{file_id}/revisar", data=datos, follow_redirects=False)


def test_subir_para_revisar_no_importa_nada(auth):
    respuesta = _subir(auth)
    assert respuesta.status_code == 303
    assert "/revisar" in respuesta.headers["location"]

    with SessionLocal() as db:
        record = db.execute(select(UploadedFile)).scalars().one()
        assert record.imported is False
        assert record.row_count == 3  # lo que se leyo
        assert db.execute(select(Transaction)).scalars().all() == []
    # Y el dashboard sigue vacio.
    assert auth.get("/api/resumen").json()["kpis"]["transactions"] == 0


def test_la_revision_muestra_las_filas_y_los_totales(auth):
    _subir(auth)
    pagina = auth.get(f"/archivos/{_file_id(auth)}/revisar").text
    assert "COMPRA COTO DIGITAL" in pagina
    assert "ARRASTRE EJERCICIO ANTERIOR" in pagina
    # El total por moneda es lo que permite comprobarlo contra el resumen.
    assert "1.333.200,50 ARS" in pagina


def test_lo_desmarcado_no_entra(auth):
    _subir(auth)
    file_id = _file_id(auth)
    respuesta = _importar(auth, file_id, excluir=[2])
    assert respuesta.status_code == 303
    destino = unquote(respuesta.headers["location"])
    assert "2 movimientos importados" in destino
    assert "1 lineas dejadas afuera" in destino

    filas = {f["descripcion"] for f in auth.get("/api/transacciones").json()["rows"]}
    assert filas == {"COMPRA COTO DIGITAL", "YPF SERVICIOS SRL"}
    with SessionLocal() as db:
        assert db.execute(select(UploadedFile)).scalars().one().imported is True


def test_volver_a_leer_no_importa(auth):
    _subir(auth)
    file_id = _file_id(auth)
    respuesta = auth.post(
        f"/archivos/{file_id}/revisar",
        data={"accion": "leer", "date": "fecha", "amount": "importe",
              "description": "concepto", "default_currency": "ARS"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert "/revisar" in respuesta.headers["location"]  # se queda en la revision
    with SessionLocal() as db:
        assert db.execute(select(Transaction)).scalars().all() == []
        assert db.execute(select(UploadedFile)).scalars().one().imported is False


def test_cambiar_el_mapeo_cambia_lo_que_se_lee(auth):
    """Si la descripcion se mapea a otra columna, la revision lo refleja."""
    _subir(auth)
    file_id = _file_id(auth)
    auth.post(
        f"/archivos/{file_id}/revisar",
        data={"accion": "leer", "date": "fecha", "amount": "importe",
              "description": "persona", "default_currency": "ARS"},
        follow_redirects=False,
    )
    # El mapeo queda guardado, asi que la revision ya lo muestra aplicado.
    pagina = auth.get(f"/archivos/{file_id}/revisar").text
    assert "COMPRA COTO DIGITAL" not in pagina
    assert "Ana" in pagina


def test_varios_archivos_se_revisan_en_fila(auth):
    _subir(auth, "marzo.csv")
    _subir(auth, "abril.csv")
    with SessionLocal() as db:
        ids = [r.id for r in db.execute(select(UploadedFile).order_by(UploadedFile.id)).scalars()]

    respuesta = _importar(auth, ids[0])
    destino = unquote(respuesta.headers["location"])
    # Al terminar uno se pasa al siguiente sin volver a la lista.
    assert f"/archivos/{ids[1]}/revisar" in destino
    assert "Sigue el proximo archivo" in destino

    respuesta = _importar(auth, ids[1])
    assert unquote(respuesta.headers["location"]).startswith("/archivos?message=")


def test_sin_revisar_se_importa_directo(auth):
    """Destildar la casilla mantiene el camino corto de siempre."""
    respuesta = _subir(auth, revisar="")
    assert respuesta.status_code == 303
    assert "/revisar" not in respuesta.headers["location"]
    assert auth.get("/api/transacciones").json()["total"] == 3
    with SessionLocal() as db:
        assert db.execute(select(UploadedFile)).scalars().one().imported is True


def test_un_archivo_ya_importado_se_puede_revisar_de_nuevo(auth):
    _subir(auth, revisar="")
    file_id = _file_id(auth)
    pagina = auth.get(f"/archivos/{file_id}/revisar").text
    assert "ya esta importado" in pagina

    # Reimportar dejando una fuera reemplaza los movimientos del archivo.
    _importar(auth, file_id, excluir=[0])
    assert auth.get("/api/transacciones").json()["total"] == 2


def test_los_pendientes_no_cuentan_como_archivos_del_dashboard(auth):
    _subir(auth)
    pagina = auth.get("/").text
    assert "0 de 0 archivos" in pagina
    assert "sin importar" in pagina

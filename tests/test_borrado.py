"""Borrado manual de movimientos desde la tabla del dashboard."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Transaction, UploadedFile

from tests.conftest import reset_db

# Dos lineas de totales repetidas, que es como se cuelan en los resumenes.
CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,TOTAL DEL MES,Ana,100000.00\n"
    "2026-03-03,YPF SERVICIOS SRL,Ana,35000.00\n"
    "2026-04-02,TOTAL DEL MES,Ana,120000.00\n"
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
        client.post(
            "/archivos/upload",
            files={"files": ("marzo.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
            data={"default_person": "", "default_currency": "ARS"},
            follow_redirects=False,
        )
        yield client


def _filas(client) -> dict[str, int]:
    return {f["descripcion"]: f["id"] for f in client.get("/api/transacciones").json()["rows"]}


def test_borrar_un_movimiento(auth):
    filas = _filas(auth)
    respuesta = auth.delete(f"/api/transacciones/{filas['COMPRA COTO DIGITAL']}")
    assert respuesta.status_code == 200
    assert respuesta.json()["borrados"] == 1

    quedan = auth.get("/api/transacciones").json()
    assert quedan["total"] == 3
    assert "COMPRA COTO DIGITAL" not in {f["descripcion"] for f in quedan["rows"]}
    # Los totales del dashboard se recalculan con lo que queda.
    assert auth.get("/api/resumen").json()["kpis"]["total_expense"] == pytest.approx(255000.00)


def test_borrar_todos_los_que_dicen_lo_mismo(auth):
    filas = _filas(auth)
    respuesta = auth.delete(f"/api/transacciones/{filas['TOTAL DEL MES']}?similares=1")
    assert respuesta.status_code == 200
    # Las dos lineas de totales, aunque tengan importe y mes distintos.
    assert respuesta.json()["borrados"] == 2

    quedan = _filas(auth)
    assert "TOTAL DEL MES" not in quedan
    assert set(quedan) == {"COMPRA COTO DIGITAL", "YPF SERVICIOS SRL"}


def test_el_archivo_actualiza_su_contador(auth):
    filas = _filas(auth)
    auth.delete(f"/api/transacciones/{filas['TOTAL DEL MES']}?similares=1")
    with SessionLocal() as db:
        record = db.execute(select(UploadedFile)).scalars().one()
        assert record.row_count == 2
    # Y la pantalla de Archivos lo muestra.
    assert "2 movimientos" in auth.get("/archivos").text


def test_borrar_algo_que_no_existe(auth):
    assert auth.delete("/api/transacciones/99999").status_code == 404


def test_sin_sesion_no_se_puede_borrar(auth):
    filas = _filas(auth)
    anonimo = TestClient(app)
    respuesta = anonimo.delete(
        f"/api/transacciones/{filas['COMPRA COTO DIGITAL']}",
        headers={"Accept": "application/json"},
    )
    assert respuesta.status_code == 401
    with SessionLocal() as db:
        assert len(db.execute(select(Transaction)).scalars().all()) == 4

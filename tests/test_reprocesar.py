"""Reprocesar archivos ya subidos con el lector actual.

El archivo original se guarda: cuando mejora la lectura de un formato no
tiene sentido obligar a borrar y volver a subir.
"""
from __future__ import annotations

import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ingest
from app.db import SessionLocal
from app.main import app
from app.models import Transaction, UploadedFile

from tests.conftest import reset_db

CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,YPF SERVICIOS SRL,Miguel,35000.00\n"
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


def test_reprocesar_vuelve_a_leer_el_archivo_guardado(auth):
    # Un movimiento borrado a mano vuelve al reprocesar: se relee el original.
    filas = {f["descripcion"]: f["id"] for f in auth.get("/api/transacciones").json()["rows"]}
    auth.delete(f"/api/transacciones/{filas['COMPRA COTO DIGITAL']}")
    assert auth.get("/api/transacciones").json()["total"] == 1

    respuesta = auth.post("/archivos/reprocesar", follow_redirects=False)
    assert respuesta.status_code == 303
    assert "error" not in respuesta.headers["location"]
    assert auth.get("/api/transacciones").json()["total"] == 2


def test_reprocesar_conserva_las_categorias_corregidas_a_mano(auth):
    filas = {f["descripcion"]: f["id"] for f in auth.get("/api/transacciones").json()["rows"]}
    auth.patch(
        f"/api/transacciones/{filas['YPF SERVICIOS SRL']}",
        json={"categoria": "Auto", "aplicar_a_similares": True, "patron": "YPF SERVICIOS SRL"},
    )
    auth.post("/archivos/reprocesar", follow_redirects=False)

    categorias = {
        f["descripcion"]: f["categoria"] for f in auth.get("/api/transacciones").json()["rows"]
    }
    # La correccion vive como regla, asi que sobrevive a releer el archivo.
    assert categorias["YPF SERVICIOS SRL"] == "Auto"


def test_reprocesar_un_archivo_suelto(auth):
    with SessionLocal() as db:
        file_id = db.execute(select(UploadedFile)).scalars().one().id
    respuesta = auth.post(f"/archivos/{file_id}/reprocesar", follow_redirects=False)
    assert respuesta.status_code == 303
    assert "2 movimientos" in unquote(respuesta.headers["location"])


def test_si_el_archivo_ya_no_esta_lo_dice(auth, monkeypatch):
    monkeypatch.setattr("app.routers.files._stored_file", lambda record: None)
    respuesta = auth.post("/archivos/reprocesar", follow_redirects=False)
    destino = unquote(respuesta.headers["location"])
    assert "error=" in destino
    assert "ya no esta guardado" in destino
    # Y los movimientos que habia siguen ahi: un reproceso fallido no borra.
    with SessionLocal() as db:
        assert len(db.execute(select(Transaction)).scalars().all()) == 2


def test_sin_archivos_no_hace_nada(auth):
    with SessionLocal() as db:
        file_id = db.execute(select(UploadedFile)).scalars().one().id
    auth.post(f"/archivos/{file_id}/delete", follow_redirects=False)
    respuesta = auth.post("/archivos/reprocesar", follow_redirects=False)
    assert "error=No hay archivos" in unquote(respuesta.headers["location"])

"""Subida de varios archivos en una sola pasada."""
from __future__ import annotations

import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Transaction, UploadedFile

from tests.conftest import reset_db

MARZO = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,YPF SERVICIOS SRL,Miguel,35000.00\n"
)
ABRIL = (
    "fecha,concepto,persona,importe\n"
    "2026-04-01,NETFLIX.COM,Ana,12990.00\n"
    "2026-04-02,FARMACITY SUCURSAL 44,Ana,18300.00\n"
    "2026-04-03,PAGO ALQUILER ABRIL,Miguel,480000.00\n"
)


@pytest.fixture
def auth():
    reset_db()
    # reset_db limpia la base pero no el disco, y aca se cuenta lo que
    # queda guardado para comprobar que un archivo rechazado no deja rastro.
    for sobrante in get_settings().upload_dir.iterdir():
        if sobrante.is_file():
            sobrante.unlink()
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        yield client


def _subir(client, *archivos):
    return client.post(
        "/archivos/upload",
        files=[
            ("files", (nombre, io.BytesIO(contenido), tipo)) for nombre, contenido, tipo in archivos
        ],
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )


def _csv(nombre: str, texto: str):
    return (nombre, texto.encode("utf-8"), "text/csv")


def _guardados() -> int:
    """Cuantos archivos hay realmente en la carpeta de subidas."""
    return len([p for p in get_settings().upload_dir.iterdir() if p.is_file()])


def test_dos_archivos_de_una(auth):
    response = _subir(auth, _csv("marzo.csv", MARZO), _csv("abril.csv", ABRIL))
    assert response.status_code == 303
    destino = unquote(response.headers["location"])
    assert "error=" not in destino
    assert "2 archivos" in destino
    assert "5 movimientos" in destino

    with SessionLocal() as db:
        nombres = {r.filename for r in db.execute(select(UploadedFile)).scalars()}
        assert nombres == {"marzo.csv", "abril.csv"}
        assert db.execute(select(Transaction)).scalars().all().__len__() == 5

    # Cada archivo queda como una entrada propia, activable por separado.
    assert auth.get("/api/resumen").json()["kpis"]["transactions"] == 5


def test_uno_malo_no_se_lleva_puestos_a_los_demas(auth):
    response = _subir(
        auth,
        _csv("bueno.csv", MARZO),
        ("foto.png", b"x", "image/png"),
        _csv("tambien-bueno.csv", ABRIL),
    )
    assert response.status_code == 303
    destino = unquote(response.headers["location"])
    # Se importa lo que se pudo y se dice exactamente que quedo afuera.
    assert "message=" in destino
    assert "2 archivos" in destino
    assert "foto.png" in destino

    with SessionLocal() as db:
        nombres = {r.filename for r in db.execute(select(UploadedFile)).scalars()}
    assert nombres == {"bueno.csv", "tambien-bueno.csv"}
    # El archivo rechazado no deja basura en el disco.
    assert _guardados() == 2


def test_si_fallan_todos_se_avisa_el_motivo(auth):
    response = _subir(auth, ("foto.png", b"x", "image/png"), ("notas.docx", b"x", "app/docx"))
    assert response.status_code == 303
    destino = unquote(response.headers["location"])
    assert "error=" in destino
    assert "foto.png" in destino and "notas.docx" in destino

    with SessionLocal() as db:
        assert db.execute(select(UploadedFile)).scalars().all() == []
    assert _guardados() == 0


def test_un_solo_archivo_sigue_funcionando(auth):
    response = _subir(auth, _csv("marzo.csv", MARZO))
    assert response.status_code == 303
    destino = unquote(response.headers["location"])
    assert "2 filas de 'marzo.csv'" in destino

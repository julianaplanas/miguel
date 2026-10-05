"""Subir dos veces el mismo archivo no puede duplicar los movimientos.

Es el error mas facil de cometer y el mas dificil de notar: todo parece
correcto salvo que los totales estan al doble.
"""
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
    "fecha,concepto,categoria,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Supermercado,Ana,48200.50\n"
    "2026-03-02,YPF SERVICIOS SRL,Transporte,Ana,35000.00\n"
)
# Mismos movimientos mas uno nuevo: el solapamiento tipico de dos resumenes.
CSV_SOLAPADO = CSV + "2026-03-03,FARMACITY,Salud,Ana,18300.00\n"


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


def _subir(client, nombre, csv=CSV, revisar=""):
    datos = {"default_person": "", "default_currency": "ARS"}
    if revisar:
        datos["revisar"] = revisar
    return client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data=datos,
        follow_redirects=False,
    )


def _movimientos(client) -> int:
    return client.get("/api/transacciones").json()["total"]


def test_el_mismo_archivo_dos_veces_no_se_importa_solo(auth):
    _subir(auth, "marzo.csv")
    assert _movimientos(auth) == 2

    respuesta = _subir(auth, "marzo.csv")
    assert respuesta.status_code == 303
    # No se importo nada y se va a la revision con el aviso.
    assert _movimientos(auth) == 2
    destino = unquote(respuesta.headers["location"])
    assert "/revisar" in destino
    assert "mismo contenido" in destino


def test_lo_detecta_aunque_cambie_el_nombre(auth):
    """Lo que se compara es el contenido, no como se llama el archivo."""
    _subir(auth, "resumen.csv")
    respuesta = _subir(auth, "resumen (1).csv")
    assert "mismo contenido" in unquote(respuesta.headers["location"])
    assert _movimientos(auth) == 2


def test_la_revision_lo_dice_con_nombre_y_fecha(auth):
    _subir(auth, "marzo.csv")
    _subir(auth, "copia.csv")
    with SessionLocal() as db:
        pendiente = (
            db.execute(select(UploadedFile).where(UploadedFile.imported.is_(False)))
            .scalars()
            .one()
        )
    pagina = auth.get(f"/archivos/{pendiente.id}/revisar").text
    assert "Este archivo ya lo subiste" in pagina
    assert "marzo.csv" in pagina
    assert "descartarlo" in pagina


def test_un_archivo_que_se_superpone_tampoco_entra_solo(auth):
    """Otro contenido, pero con movimientos que ya estaban."""
    _subir(auth, "marzo.csv")
    respuesta = _subir(auth, "marzo-y-algo-mas.csv", CSV_SOLAPADO)

    assert _movimientos(auth) == 2  # no se importo nada todavia
    destino = unquote(respuesta.headers["location"])
    assert "/revisar" in destino
    assert "2 movimientos que ya estaban" in destino


def test_en_la_revision_se_pueden_desmarcar_los_repetidos(auth):
    _subir(auth, "marzo.csv")
    _subir(auth, "marzo-y-algo-mas.csv", CSV_SOLAPADO)
    with SessionLocal() as db:
        pendiente = (
            db.execute(select(UploadedFile).where(UploadedFile.imported.is_(False)))
            .scalars()
            .one()
        )
    pagina = auth.get(f"/archivos/{pendiente.id}/revisar").text
    assert "Desmarcar 2 duplicadas" in pagina

    # Se importan solo las que no estaban: queda el movimiento nuevo.
    auth.post(
        f"/archivos/{pendiente.id}/revisar",
        data={
            "accion": "importar",
            "date": "fecha", "amount": "importe", "description": "concepto",
            "category": "categoria", "person": "persona",
            "default_currency": "ARS",
            "excluir": ["0", "1"],
        },
        follow_redirects=False,
    )
    assert _movimientos(auth) == 3
    descripciones = [f["descripcion"] for f in auth.get("/api/transacciones").json()["rows"]]
    assert descripciones.count("COMPRA COTO DIGITAL") == 1


def test_un_archivo_distinto_entra_directo_como_siempre(auth):
    _subir(auth, "marzo.csv")
    otro = (
        "fecha,concepto,categoria,persona,importe\n"
        "2026-04-01,NETFLIX.COM,Suscripciones,Ana,12990.00\n"
    )
    respuesta = _subir(auth, "abril.csv", otro)
    assert "/revisar" not in respuesta.headers["location"]
    assert _movimientos(auth) == 3


def test_si_se_quiere_igual_se_importa_desde_la_revision(auth):
    """El aviso no es una prohibicion: la decision sigue siendo tuya."""
    _subir(auth, "marzo.csv")
    _subir(auth, "copia.csv")
    with SessionLocal() as db:
        pendiente = (
            db.execute(select(UploadedFile).where(UploadedFile.imported.is_(False)))
            .scalars()
            .one()
        )
    auth.post(
        f"/archivos/{pendiente.id}/revisar",
        data={
            "accion": "importar",
            "date": "fecha", "amount": "importe", "description": "concepto",
            "category": "categoria", "person": "persona",
            "default_currency": "ARS",
        },
        follow_redirects=False,
    )
    assert _movimientos(auth) == 4


def test_se_detecta_contra_archivos_subidos_antes_de_existir_la_huella(auth):
    """A los archivos viejos se les calcula la huella la primera vez."""
    _subir(auth, "marzo.csv")
    with SessionLocal() as db:
        viejo = db.execute(select(UploadedFile)).scalars().one()
        viejo.content_hash = ""  # como si se hubiera subido antes del cambio
        db.commit()

    respuesta = _subir(auth, "otra-vez.csv")
    assert "mismo contenido" in unquote(respuesta.headers["location"])
    with SessionLocal() as db:
        anterior = db.execute(
            select(UploadedFile).where(UploadedFile.filename == "marzo.csv")
        ).scalars().one()
        assert anterior.content_hash  # quedo calculada

"""Comprobar lo extraido antes de importarlo.

Las tres preguntas que importan al mirar un archivo recien leido: ¿esta
todo?, ¿esta dos veces?, ¿de donde salio cada fila?
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import UploadedFile

from tests.conftest import reset_db
from tests.pdf_fixtures import resumen_tarjeta_dos_columnas

CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,YPF SERVICIOS SRL,Ana,35000.00\n"
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


def _subir(client, nombre, contenido, tipo="text/csv", revisar="1"):
    datos = {"default_person": "", "default_currency": "ARS"}
    if revisar:
        datos["revisar"] = revisar
    return client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(contenido), tipo)},
        data=datos,
        follow_redirects=False,
    )


def _ultimo_id() -> int:
    with SessionLocal() as db:
        return db.execute(select(UploadedFile).order_by(UploadedFile.id.desc())).scalars().first().id


def test_el_total_del_documento_confirma_la_lectura(auth):
    """El resumen declara su total: si coincide, no falta ni sobra nada."""
    _subir(auth, "master.pdf", resumen_tarjeta_dos_columnas(), "application/pdf")
    pagina = auth.get(f"/archivos/{_ultimo_id()}/revisar").text

    assert "Contra los totales del documento" in pagina
    assert "TOTAL A PAGAR" in pagina
    assert "coincide al centavo" in pagina
    # El otro total del documento no cuadra por 1.000 y se ve la diferencia.
    assert "TOTAL DEL MES" in pagina
    assert "1.000,00 ARS" in pagina


def test_si_no_cuadra_ningun_total_se_avisa(auth):
    """Un CSV no declara totales: no hay nada contra que comprobar."""
    _subir(auth, "marzo.csv", CSV.encode("utf-8"))
    pagina = auth.get(f"/archivos/{_ultimo_id()}/revisar").text
    assert "Contra los totales del documento" not in pagina


def test_subir_dos_veces_el_mismo_archivo_avisa(auth):
    _subir(auth, "marzo.csv", CSV.encode("utf-8"), revisar="")
    _subir(auth, "marzo-otra-vez.csv", CSV.encode("utf-8"))

    pagina = auth.get(f"/archivos/{_ultimo_id()}/revisar").text
    assert "Desmarcar 2 duplicadas" in pagina
    assert "ya hay un movimiento igual importado" in pagina


def test_revisar_un_archivo_ya_importado_no_lo_marca_duplicado(auth):
    """Sus propios movimientos no cuentan: si no, todo pareceria repetido."""
    _subir(auth, "marzo.csv", CSV.encode("utf-8"), revisar="")
    pagina = auth.get(f"/archivos/{_ultimo_id()}/revisar").text
    assert "ya hay un movimiento igual importado" not in pagina
    assert "Desmarcar 1 duplicada" not in pagina


def test_se_avisa_de_las_filas_sin_fecha(auth):
    csv = "fecha,concepto,importe\n,SIN FECHA SRL,1000.00\n2026-03-01,COTO,2000.00\n"
    _subir(auth, "raro.csv", csv.encode("utf-8"))
    pagina = auth.get(f"/archivos/{_ultimo_id()}/revisar").text
    assert "sin fecha" in pagina


def test_cada_fila_muestra_de_donde_salio(auth):
    """El origen es la fila cruda del archivo, con todas sus columnas."""
    csv = "fecha,concepto,importe,saldo\n2026-03-01,COTO,2000.00,999999\n"
    _subir(auth, "con-saldo.csv", csv.encode("utf-8"))
    pagina = auth.get(f"/archivos/{_ultimo_id()}/revisar").text
    # La columna 'saldo' no se mapea a nada, pero en el origen esta.
    assert "999999" in pagina

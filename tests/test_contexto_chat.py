"""Lo que el modelo recibe en el chat: que desgloses existen y que no.

El bug que motiva esto: preguntado "por banco", el modelo invento bancos y
cifras con dos decimales, porque el contexto no traia nada por archivo y el
prompt no decia que desgloses existen.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.llm import SYSTEM_PROMPT, build_data_context, build_messages
from app.main import app

from tests.conftest import reset_db

GALICIA = (
    "fecha,concepto,importe,moneda\n"
    "2026-05-02,COTO,48200.50,ARS\n"
    "2026-05-05,NETFLIX,9.99,USD\n"
    "2026-05-09,SUELDO,-900000,ARS\n"
)
SANTANDER = (
    "fecha,concepto,cuenta,importe\n"
    "2026-05-03,YPF,CA 123,35000\n"
    "2026-05-04,FARMACITY,CA 123,18300\n"
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


def _subir(client, nombre, contenido):
    client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(contenido.encode()), "text/csv")},
        data={"revisar": ""},
        follow_redirects=False,
    )


def test_el_contexto_trae_el_desglose_por_archivo_sin_mezclar_monedas(auth):
    _subir(auth, "galicia-mayo.csv", GALICIA)
    _subir(auth, "santander-mayo.csv", SANTANDER)
    with SessionLocal() as db:
        contexto = build_data_context(db)

    assert contexto["archivos_activos"] == 2
    assert contexto["movimientos_totales"] == 5
    por_archivo = {(a["nombre"], a["moneda"]): a for a in contexto["por_archivo"]}
    assert por_archivo[("galicia-mayo.csv", "ARS")]["gasto"] == pytest.approx(48200.50)
    assert por_archivo[("galicia-mayo.csv", "ARS")]["ingreso"] == pytest.approx(900000)
    assert por_archivo[("galicia-mayo.csv", "USD")]["gasto"] == pytest.approx(9.99)
    assert por_archivo[("santander-mayo.csv", "ARS")]["movimientos"] == 2
    # La columna de cuenta solo venia en un archivo: lo que no la trae se
    # ve como tal, no se inventa.
    cuentas = {c["nombre"] for c in contexto["por_cuenta"]}
    assert cuentas == {"CA 123", "(sin dato)"}


def test_sin_columna_de_cuenta_no_hay_desglose_por_cuenta(auth):
    _subir(auth, "galicia-mayo.csv", GALICIA)
    with SessionLocal() as db:
        contexto = build_data_context(db)
    assert contexto["por_cuenta"] == []


def test_el_prompt_dice_que_desgloses_existen_y_prohibe_inventar(auth):
    _subir(auth, "galicia-mayo.csv", GALICIA)
    with SessionLocal() as db:
        mensajes = build_messages(db, [], "cuanto gaste por banco?")
    sistema = mensajes[0]["content"]
    for clave in ("por_archivo", "por_cuenta", "muestra_transacciones_recientes"):
        assert clave in SYSTEM_PROMPT
    assert "NUNCA inventes" in sistema and "bancos" in sistema
    # El JSON que ve el modelo lleva el desglose por archivo de verdad.
    datos = json.loads(sistema.split("RESUMEN DE DATOS ACTIVOS (JSON):\n", 1)[1])
    assert datos["por_archivo"][0]["nombre"] == "galicia-mayo.csv"

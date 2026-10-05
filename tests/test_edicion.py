"""Corregir un movimiento a mano: fecha, importe, moneda, persona, descripcion.

El caso que lo pide: una retencion en pesos que el lector tomo como
dolares. No se arregla con la categoria ni borrando la fila.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Transaction

from tests.conftest import reset_db

CSV = (
    "fecha,concepto,categoria,persona,importe,moneda\n"
    "2026-03-01,RETENCION IIBB,Impuestos,Ana,10000.00,USD\n"
    "2026-03-02,COMPRA COTO,Supermercado,Ana,48200.50,ARS\n"
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


def _filas(client) -> dict[str, dict]:
    return {f["descripcion"]: f for f in client.get("/api/transacciones").json()["rows"]}


def test_corregir_la_moneda(auth):
    """Lo que motivo todo: estaba en dolares y eran pesos."""
    fila = _filas(auth)["RETENCION IIBB"]
    assert fila["moneda"] == "USD"

    respuesta = auth.patch(f"/api/transacciones/{fila['id']}", json={"moneda": "ARS"})
    assert respuesta.status_code == 200
    assert respuesta.json()["fila"]["moneda"] == "ARS"

    despues = _filas(auth)["RETENCION IIBB"]
    assert despues["moneda"] == "ARS"
    assert despues["editado"] is True
    # Y el dashboard deja de contarlo como dolares.
    monedas = {m["code"] for m in auth.get("/api/resumen").json()["by_currency"]}
    assert monedas == {"ARS"}


def test_corregir_importe_fecha_y_descripcion(auth):
    fila = _filas(auth)["COMPRA COTO"]
    respuesta = auth.patch(
        f"/api/transacciones/{fila['id']}",
        json={
            "importe": "1.234,56",
            "fecha": "2026-04-15",
            "descripcion": "COTO ABASTO",
            "persona": "Miguel",
        },
    )
    assert respuesta.status_code == 200
    nueva = respuesta.json()["fila"]
    # El importe acepta el formato argentino, igual que al importar.
    assert nueva["importe"] == 1234.56
    assert nueva["fecha"] == "2026-04-15"
    assert nueva["descripcion"] == "COTO ABASTO"
    assert nueva["persona"] == "Miguel"


def test_un_ingreso_se_escribe_en_negativo(auth):
    fila = _filas(auth)["COMPRA COTO"]
    auth.patch(f"/api/transacciones/{fila['id']}", json={"importe": "-5000"})
    kpis = auth.get("/api/resumen").json()["kpis"]
    assert kpis["total_income"] == 5000.0


def test_lo_que_no_se_manda_no_se_toca(auth):
    fila = _filas(auth)["COMPRA COTO"]
    auth.patch(f"/api/transacciones/{fila['id']}", json={"moneda": "USD"})
    despues = _filas(auth)["COMPRA COTO"]
    assert despues["categoria"] == "Supermercado"
    assert despues["importe"] == 48200.50
    assert despues["fecha"] == "2026-03-02"


def test_los_valores_invalidos_se_rechazan(auth):
    fila = _filas(auth)["COMPRA COTO"]
    assert auth.patch(f"/api/transacciones/{fila['id']}", json={"importe": "no"}).status_code == 400
    assert auth.patch(f"/api/transacciones/{fila['id']}", json={"fecha": "32/13/2026"}).status_code == 400
    assert auth.patch(f"/api/transacciones/{fila['id']}", json={"moneda": ""}).status_code == 400
    assert auth.patch(f"/api/transacciones/{fila['id']}", json={"moneda": "12"}).status_code == 400
    # Y nada cambio.
    with SessionLocal() as db:
        tx = db.execute(select(Transaction).where(Transaction.id == fila["id"])).scalars().one()
        assert tx.edited is False
        assert tx.currency == "ARS"


def test_guardar_lo_mismo_no_marca_como_editado(auth):
    fila = _filas(auth)["COMPRA COTO"]
    auth.patch(f"/api/transacciones/{fila['id']}", json={"moneda": "ARS", "persona": "Ana"})
    assert _filas(auth)["COMPRA COTO"]["editado"] is False


def test_la_categoria_se_sigue_cambiando_igual(auth):
    """El editor nuevo no rompe el editor de categoria de la tabla."""
    fila = _filas(auth)["COMPRA COTO"]
    respuesta = auth.patch(
        f"/api/transacciones/{fila['id']}",
        json={"categoria": "Comida", "aplicar_a_similares": True, "patron": "COMPRA COTO"},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["aplicados"] == 1
    assert _filas(auth)["COMPRA COTO"]["categoria"] == "Comida"

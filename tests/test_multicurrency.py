"""El dashboard con pesos y dolares mezclados."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.main import app
from app.models import ExchangeRate, Transaction, UploadedFile
from app.preferences import set_base_currency

from tests.conftest import reset_db

CSV = (
    "fecha,concepto,categoria,persona,importe,moneda\n"
    "2026-03-01,Supermercado,Comida,Ana,45000,ARS\n"
    "2026-03-02,Hosting,Servicios,Miguel,12,USD\n"
    "2026-03-03,Libro,Ocio,Ana,8500,ARS\n"
)


@pytest.fixture(scope="module")
def auth():
    reset_db()
    with SessionLocal() as db:
        set_base_currency(db, "ARS")
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        response = client.post(
            "/archivos/upload",
            files={"files": ("mixto.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
            data={"default_person": "", "default_currency": "ARS"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        yield client


def test_sin_tipo_de_cambio_no_suma_monedas_distintas(auth):
    data = auth.get("/api/resumen").json()
    # 45000 + 8500 + 12 seria 53512: un numero sin sentido. No debe pasar.
    assert data["missing_rates"] == ["USD"]
    assert data["converted"] is False
    assert data["currency"] == "ARS"
    assert data["kpis"]["total_expense"] == pytest.approx(53500.00)
    assert data["kpis"]["transactions"] == 2


def test_desglose_por_moneda(auth):
    data = auth.get("/api/resumen").json()
    por_moneda = {m["code"]: m for m in data["by_currency"]}
    assert set(data["currencies"]) == {"ARS", "USD"}
    # El desglose siempre usa los importes originales, tambien el de USD.
    assert por_moneda["ARS"]["expense"] == pytest.approx(53500.00)
    assert por_moneda["USD"]["expense"] == pytest.approx(12.00)


def test_filtrar_por_una_moneda(auth):
    data = auth.get("/api/resumen", params={"moneda": "USD"}).json()
    assert data["currency"] == "USD"
    assert data["converted"] is False
    assert data["kpis"]["total_expense"] == pytest.approx(12.00)
    assert data["kpis"]["transactions"] == 1


def test_con_tipo_de_cambio_convierte_a_la_base(auth):
    response = auth.post(
        "/ajustes/tipo-de-cambio", data={"code": "USD", "rate": "1000"}, follow_redirects=False
    )
    assert response.status_code == 303

    data = auth.get("/api/resumen").json()
    assert data["converted"] is True
    assert data["currency"] == "ARS"
    assert data["missing_rates"] == []
    # 45000 + 8500 + (12 x 1000)
    assert data["kpis"]["total_expense"] == pytest.approx(65500.00)
    assert data["kpis"]["transactions"] == 3

    categorias = {c["label"]: c["value"] for c in data["by_category"]}
    assert categorias["Servicios"] == pytest.approx(12000.00)
    personas = {p["label"]: p["value"] for p in data["by_person"]}
    assert personas["Miguel"] == pytest.approx(12000.00)


def test_movimientos_muestran_la_moneda_original(auth):
    filas = auth.get("/api/transacciones").json()["rows"]
    hosting = [f for f in filas if f["descripcion"] == "Hosting"][0]
    assert hosting["moneda"] == "USD"
    assert hosting["importe"] == pytest.approx(12.00)  # sin convertir

    export = auth.get("/api/exportar.csv")
    assert "moneda" in export.text.splitlines()[0]
    assert "USD" in export.text


def test_tipo_de_cambio_invalido_no_rompe(auth):
    response = auth.post(
        "/ajustes/tipo-de-cambio", data={"code": "USD", "rate": "-5"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    # El tipo anterior sigue vigente.
    assert auth.get("/api/resumen").json()["rates"]["USD"] == pytest.approx(1000.0)


def test_pagina_de_ajustes_carga(auth):
    page = auth.get("/ajustes")
    assert page.status_code == 200
    assert "Tipos de cambio" in page.text

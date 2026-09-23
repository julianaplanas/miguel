"""Pruebas de extremo a extremo de la app (auth, subida, activacion, API)."""
from __future__ import annotations

import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.main import app

from tests.conftest import reset_db

CSV = (
    "Fecha;Concepto;Categoria;Persona;Importe\n"
    "01/03/2026;Supermercado;Comida;Ana;45,20\n"
    "05/03/2026;Cine;Ocio;Miguel;18,00\n"
    "07/04/2026;Supermercado;Comida;Miguel;60,00\n"
)


@pytest.fixture(scope="module")
def client():
    reset_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def auth(client):
    response = client.post(
        "/login",
        data={"username": "tester", "password": "secreto123", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def test_login_requerido(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_credenciales_malas(client):
    response = client.post("/login", data={"username": "tester", "password": "no", "next": "/"})
    assert response.status_code == 401


def test_api_sin_sesion_devuelve_401(client):
    fresh = TestClient(app)
    response = fresh.get("/api/resumen", headers={"Accept": "application/json"})
    assert response.status_code == 401


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_subida_y_resumen(auth):
    response = auth.post(
        "/archivos/upload",
        files={"file": ("gastos.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
        data={"default_person": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "3 filas" in unquote(response.headers["location"])

    data = auth.get("/api/resumen").json()
    assert data["kpis"]["total_expense"] == pytest.approx(123.20)
    assert data["kpis"]["transactions"] == 3
    categorias = {c["label"]: c["value"] for c in data["by_category"]}
    assert categorias["Comida"] == pytest.approx(105.20)
    personas = {p["label"]: p["value"] for p in data["by_person"]}
    assert personas["Miguel"] == pytest.approx(78.00)
    assert [m["label"] for m in data["by_month"]] == ["2026-03", "2026-04"]


def test_filtros(auth):
    data = auth.get("/api/resumen", params={"persona": "Ana"}).json()
    assert data["kpis"]["total_expense"] == pytest.approx(45.20)

    data = auth.get("/api/resumen", params={"desde": "2026-04-01"}).json()
    assert data["kpis"]["transactions"] == 1

    data = auth.get("/api/resumen", params={"categoria": "Ocio"}).json()
    assert data["kpis"]["total_expense"] == pytest.approx(18.00)


def test_transacciones_y_export(auth):
    data = auth.get("/api/transacciones", params={"limit": 2}).json()
    assert data["total"] == 3
    assert len(data["rows"]) == 2

    export = auth.get("/api/exportar.csv")
    assert export.status_code == 200
    assert "Supermercado" in export.text


def test_desactivar_archivo_vacia_el_dashboard(auth):
    files_page = auth.get("/archivos")
    assert "gastos.csv" in files_page.text

    response = auth.post("/archivos/1/toggle", data={"redirect_to": "/archivos"}, follow_redirects=False)
    assert response.status_code == 303

    data = auth.get("/api/resumen").json()
    assert data["kpis"]["transactions"] == 0

    auth.post("/archivos/1/toggle", data={"redirect_to": "/archivos"}, follow_redirects=False)
    assert auth.get("/api/resumen").json()["kpis"]["transactions"] == 3


def test_chat_sin_api_key_devuelve_502(auth):
    response = auth.post("/api/chat", json={"message": "hola"}, headers={"Accept": "application/json"})
    assert response.status_code == 502
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


def test_paginas_cargan(auth):
    for path in ("/", "/archivos", "/chat"):
        assert auth.get(path).status_code == 200


def test_borrar_archivo(auth):
    response = auth.post("/archivos/1/delete", follow_redirects=False)
    assert response.status_code == 303
    assert auth.get("/api/resumen").json()["kpis"]["transactions"] == 0

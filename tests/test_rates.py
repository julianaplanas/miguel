"""Cotizaciones automaticas y moneda base configurable.

Las llamadas HTTP se sustituyen: aqui se prueba como se interpreta la
respuesta y que pasa cuando la API falla, no la API en si.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import rates as rate_api
from app.db import SessionLocal, init_db
from app.main import app
from app.models import AppSetting, ExchangeRate, Transaction, UploadedFile
from app.preferences import base_currency, set_base_currency

CSV = (
    "fecha,concepto,categoria,persona,importe,moneda\n"
    "2026-03-01,Verduleria,Comida,Ana,45000,ARS\n"
    "2026-03-02,Hosting,Servicios,Miguel,12,USD\n"
)

RESPUESTA_BLUE = {
    "moneda": "USD",
    "casa": "blue",
    "nombre": "Blue",
    "compra": 1420.0,
    "venta": 1450.0,
    "fechaActualizacion": "2026-09-23T14:05:00.000Z",
}
RESPUESTA_OFICIAL = dict(RESPUESTA_BLUE, casa="oficial", nombre="Oficial", compra=1020.0, venta=1060.0)
RESPUESTA_ERAPI = {
    "result": "success",
    "base_code": "EUR",
    "rates": {"ARS": 1600.0, "USD": 1.08},
    "time_last_update_utc": "2026-09-23T00:00:01+00:00",
}


@pytest.fixture
def fake_api(monkeypatch):
    """Sustituye la unica salida a la red y registra las URLs pedidas."""
    llamadas: list[str] = []

    def _get(url: str):
        llamadas.append(url)
        if "/dolares/blue" in url:
            return RESPUESTA_BLUE
        if "/dolares/oficial" in url:
            return RESPUESTA_OFICIAL
        if "open.er-api.com" in url:
            return RESPUESTA_ERAPI
        raise rate_api.RateError("404 de mentira")

    monkeypatch.setattr(rate_api, "_get_json", _get)
    return llamadas


@pytest.fixture
def auth():
    init_db()
    with SessionLocal() as db:
        db.execute(delete(Transaction))
        db.execute(delete(UploadedFile))
        db.execute(delete(ExchangeRate))
        db.execute(delete(AppSetting))
        db.commit()
        set_base_currency(db, "ARS")
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        client.post(
            "/archivos/upload",
            files={"file": ("mixto.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
            data={"default_person": "", "default_currency": "ARS"},
            follow_redirects=False,
        )
        yield client


def test_dolarapi_usa_la_venta(fake_api):
    obtenida = rate_api.fetch_rate("USD", "ARS", "dolarapi:blue")
    assert obtenida.rate == 1450.0  # la venta, no la compra
    assert obtenida.source == "dolarapi:blue"
    assert "compra 1420" in obtenida.detail
    assert fake_api == ["https://dolarapi.com/v1/dolares/blue"]


def test_la_casa_elegida_cambia_el_valor(fake_api):
    assert rate_api.fetch_rate("USD", "ARS", "dolarapi:oficial").rate == 1060.0
    assert rate_api.fetch_rate("USD", "ARS", "dolarapi:blue").rate == 1450.0


def test_origen_por_defecto_segun_la_base(fake_api):
    # Con base ARS el dolar va por dolarapi; el resto, por el generico.
    assert rate_api.fetch_rate("USD", "ARS").source == "dolarapi:blue"
    assert rate_api.fetch_rate("EUR", "USD").source == "erapi"


def test_erapi_lee_la_tabla_de_la_base(fake_api):
    obtenida = rate_api.fetch_rate("EUR", "ARS", "erapi")
    assert obtenida.rate == 1600.0


def test_dolarapi_no_sirve_con_otra_base():
    with pytest.raises(rate_api.RateError):
        rate_api.fetch_rate("USD", "EUR", "dolarapi:blue")


def test_origenes_ofrecidos():
    valores = [v for v, _ in rate_api.available_sources("USD", "ARS")]
    assert valores[0] == "manual"
    assert "dolarapi:blue" in valores and "dolarapi:oficial" in valores
    # El dolar no se cotiza contra si mismo.
    assert rate_api.available_sources("USD", "USD") == [("manual", "A mano")]


def test_traer_cotizacion_desde_la_ui(auth, fake_api):
    response = auth.post(
        "/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "error" not in response.headers["location"]

    data = auth.get("/api/resumen").json()
    assert data["converted"] is True
    assert data["rates"]["USD"] == 1450.0
    # 45000 + 12 x 1450
    assert data["kpis"]["total_expense"] == pytest.approx(62400.00)


def test_si_la_api_falla_el_valor_anterior_se_mantiene(auth, fake_api, monkeypatch):
    auth.post("/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"},
              follow_redirects=False)

    def _falla(url: str):
        raise rate_api.RateError("La API respondio 503")

    monkeypatch.setattr(rate_api, "_get_json", _falla)
    response = auth.post(
        "/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    assert auth.get("/api/resumen").json()["rates"]["USD"] == 1450.0


def test_actualizar_todas_respeta_los_valores_a_mano(auth, fake_api):
    auth.post("/ajustes/tipo-de-cambio", data={"code": "USD", "rate": "999", "source": "manual"},
              follow_redirects=False)
    auth.post("/ajustes/cotizaciones", follow_redirects=False)
    # Sigue el valor puesto a mano: no se pisa.
    assert auth.get("/api/resumen").json()["rates"]["USD"] == 999.0


def test_moneda_nueva_sin_guardar_si_se_trae(auth, fake_api):
    """Si la moneda esta en los datos y no tiene tipo, se trae de la API."""
    auth.post("/ajustes/cotizaciones", follow_redirects=False)
    assert auth.get("/api/resumen").json()["rates"]["USD"] == 1450.0


def test_cambiar_la_moneda_base_desde_la_ui(auth):
    response = auth.post("/ajustes/moneda-base", data={"code": "USD"}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        assert base_currency(db) == "USD"
    assert auth.get("/api/opciones").json()["base_currency"] == "USD"


def test_cambiar_la_base_invalida_los_tipos_viejos(auth, fake_api):
    auth.post("/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"},
              follow_redirects=False)
    auth.post("/ajustes/moneda-base", data={"code": "USD"}, follow_redirects=False)
    # El tipo guardado era "1 USD = 1450 ARS"; con base USD ya no significa nada.
    data = auth.get("/api/resumen").json()
    assert "ARS" in data["missing_rates"]


def test_moneda_base_no_reconocida(auth):
    response = auth.post("/ajustes/moneda-base", data={"code": "???"}, follow_redirects=False)
    assert "error" in response.headers["location"]

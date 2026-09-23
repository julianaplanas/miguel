"""Conversion con el tipo de cambio de la fecha de cada movimiento."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import rates as rate_api
from app.db import SessionLocal, init_db
from app.main import app
from app.models import AppSetting, ExchangeRate, RateHistory, Transaction, UploadedFile
from app.preferences import set_base_currency

from tests.conftest import reset_db

# El mismo gasto en dolares cada mes, mas un alquiler fijo en pesos.
# Convertido al tipo del dia, el total mensual sube con el dolar; al tipo
# de hoy, los tres meses salen iguales.
CSV = (
    "fecha,concepto,categoria,persona,importe,moneda\n"
    "2026-01-15,Hosting,Servicios,Miguel,100,USD\n"
    "2026-01-20,Alquiler,Vivienda,Ana,50000,ARS\n"
    "2026-02-15,Hosting,Servicios,Miguel,100,USD\n"
    "2026-02-20,Alquiler,Vivienda,Ana,50000,ARS\n"
    "2026-03-15,Hosting,Servicios,Miguel,100,USD\n"
    "2026-03-20,Alquiler,Vivienda,Ana,50000,ARS\n"
)

# El dolar sube de 1000 a 1400 en el trimestre.
SERIE = {
    "2026-01-10": 1000.0,
    "2026-02-10": 1200.0,
    "2026-03-10": 1400.0,
}


@pytest.fixture
def fake_api(monkeypatch):
    def _get(url: str, timeout: float = 15.0):
        if "argentinadatos" in url:
            return [
                {"casa": "blue", "compra": valor - 30, "venta": valor, "fecha": fecha}
                for fecha, valor in SERIE.items()
            ]
        if "/dolares/blue" in url:
            return {"casa": "blue", "nombre": "Blue", "compra": 1470.0, "venta": 1500.0,
                    "fechaActualizacion": "2026-09-23T14:00:00.000Z"}
        raise rate_api.RateError("fuera de alcance")

    monkeypatch.setattr(rate_api, "_get_json", _get)


@pytest.fixture
def auth():
    reset_db()
    with SessionLocal() as db:
        set_base_currency(db, "ARS")
    with TestClient(app) as client:
        client.post("/login", data={"username": "tester", "password": "secreto123", "next": "/"},
                    follow_redirects=False)
        client.post(
            "/archivos/upload",
            files={"file": ("usd.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
            data={"default_person": "", "default_currency": "ARS"},
            follow_redirects=False,
        )
        yield client


def test_descargar_historico(auth, fake_api):
    response = auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error" not in response.headers["location"]
    with SessionLocal() as db:
        filas = db.execute(RateHistory.__table__.select()).all()
    assert len(filas) == 3
    assert {f.source for f in filas} == {"argentinadatos:blue"}


def test_cada_gasto_usa_el_tipo_de_su_fecha(auth, fake_api):
    auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    auth.post("/ajustes/modo-conversion", data={"mode": "historical"}, follow_redirects=False)

    data = auth.get("/api/resumen").json()
    assert data["mode"] == "historical"
    assert data["currency"] == "ARS"
    assert data["historical_fallbacks"] == 0

    por_mes = {m["label"]: m["expense"] for m in data["by_month"]}
    # Cada mes: 50.000 de alquiler + 100 USD al tipo de ESE mes.
    assert por_mes["2026-01"] == pytest.approx(150000.0)
    assert por_mes["2026-02"] == pytest.approx(170000.0)
    assert por_mes["2026-03"] == pytest.approx(190000.0)
    assert data["kpis"]["total_expense"] == pytest.approx(510000.0)


def test_el_modo_actual_aplana_la_evolucion(auth, fake_api):
    """Con el tipo de hoy los tres meses salen iguales, que es lo que engana."""
    auth.post("/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"},
              follow_redirects=False)
    auth.post("/ajustes/modo-conversion", data={"mode": "current"}, follow_redirects=False)

    data = auth.get("/api/resumen").json()
    por_mes = {m["label"]: m["expense"] for m in data["by_month"]}
    # 50.000 + 100 x 1500 en los tres meses: la subida del dolar desaparece.
    assert por_mes["2026-01"] == por_mes["2026-03"] == pytest.approx(200000.0)


def test_fin_de_semana_usa_la_cotizacion_anterior(auth, fake_api):
    auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    auth.post("/ajustes/modo-conversion", data={"mode": "historical"}, follow_redirects=False)
    # El 15/01 no cotiza en la serie: se usa la del 10/01 (1000).
    data = auth.get("/api/resumen").json()
    por_mes = {m["label"]: m["expense"] for m in data["by_month"]}
    assert por_mes["2026-01"] == pytest.approx(150000.0)


def test_sin_historico_cae_al_tipo_actual_y_lo_avisa(auth, fake_api):
    auth.post("/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"},
              follow_redirects=False)
    auth.post("/ajustes/modo-conversion", data={"mode": "historical"}, follow_redirects=False)

    data = auth.get("/api/resumen").json()
    assert data["historical_fallbacks"] == 3
    assert data["fallback_currencies"] == ["USD"]
    # 150.000 de alquiler + 300 USD x 1500
    assert data["kpis"]["total_expense"] == pytest.approx(600000.0)


def test_movimiento_anterior_al_historico_cae_al_actual(auth, fake_api):
    auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    auth.post("/ajustes/cotizacion", data={"code": "USD", "source": "dolarapi:blue"},
              follow_redirects=False)
    auth.post("/ajustes/modo-conversion", data={"mode": "historical"}, follow_redirects=False)
    with SessionLocal() as db:
        db.add(Transaction(
            file_id=db.execute(UploadedFile.__table__.select()).one().id,
            date=dt.date(2025, 6, 1), description="Viejo", amount=10.0,
            currency="USD", category="Servicios", person="Miguel",
        ))
        db.commit()
    data = auth.get("/api/resumen").json()
    assert data["historical_fallbacks"] == 1  # solo el de 2025


def test_borrar_historico(auth, fake_api):
    auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    auth.post("/ajustes/historico/borrar", data={"code": "USD"}, follow_redirects=False)
    with SessionLocal() as db:
        assert db.execute(RateHistory.__table__.select()).all() == []


def test_historico_sin_movimientos_con_fecha(auth, fake_api):
    with SessionLocal() as db:
        db.execute(delete(Transaction))
        db.commit()
    response = auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    assert "error" in response.headers["location"]


def test_una_sola_moneda_no_se_convierte(auth, fake_api):
    """Si todo esta en dolares, se muestra en dolares: no hay nada que juntar."""
    auth.post("/ajustes/historico", data={"code": "USD"}, follow_redirects=False)
    auth.post("/ajustes/modo-conversion", data={"mode": "historical"}, follow_redirects=False)

    data = auth.get("/api/resumen", params={"moneda": "USD"}).json()
    assert data["currency"] == "USD"
    assert data["converted"] is False
    assert data["kpis"]["total_expense"] == pytest.approx(300.0)  # 3 x 100, sin tocar

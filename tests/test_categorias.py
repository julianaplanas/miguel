"""Deduccion de categorias y correccion manual."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.categorize import SOURCE_MANUAL, SOURCE_RULE, Rule, categorize, default_rules, sort_rules
from app.db import SessionLocal
from app.main import app
from app.models import CategoryRule, Transaction
from app.rules import load_rules, recategorize, save_rule

from tests.conftest import reset_db

# Descripciones tal como las imprime un banco argentino: sin columna de
# categoria, en mayusculas y abreviadas.
CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,YPF SERVICIOS SRL,Miguel,35000.00\n"
    "2026-03-03,NETFLIX.COM,Ana,12990.00\n"
    "2026-03-04,DEBITO AUTOMATICO EDENOR,Miguel,32150.00\n"
    "2026-03-05,FARMACITY SUCURSAL 44,Ana,18300.00\n"
    "2026-03-06,PAGO ALQUILER MARZO,Miguel,480000.00\n"
    "2026-03-07,QWERTY SRL 00012345,Ana,9500.00\n"
)


@pytest.fixture
def auth():
    reset_db()
    with TestClient(app) as client:
        client.post("/login", data={"username": "tester", "password": "secreto123", "next": "/"},
                    follow_redirects=False)
        client.post(
            "/archivos/upload",
            files={"file": ("extracto.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
            data={"default_person": "", "default_currency": "ARS"},
            follow_redirects=False,
        )
        yield client


def _categorias(client) -> dict[str, str]:
    filas = client.get("/api/transacciones").json()["rows"]
    return {f["descripcion"]: f["categoria"] for f in filas}


# --------------------------- reglas ---------------------------

def test_reglas_de_fabrica_reconocen_comercios_argentinos():
    reglas = sort_rules(default_rules())
    casos = {
        "COMPRA COTO DIGITAL": "Supermercado",
        "YPF SERVICIOS SRL": "Transporte",
        "NETFLIX.COM": "Suscripciones",
        "DEBITO AUTOMATICO EDENOR": "Servicios",
        "FARMACITY SUCURSAL 44": "Salud",
        "PAGO ALQUILER MARZO": "Vivienda",
        "PEAJE AUSA ILLIA": "Transporte",
        "TRANSFERENCIA RECIBIDA": "Transferencias",
        # 'transferencia' es una palabra mas larga que 'sueldo', pero esto
        # es un ingreso: la prioridad manda sobre la longitud.
        "TRANSFERENCIA RECIBIDA SUELDO": "Ingresos",
        "ACREDITACION HABERES": "Ingresos",
        "DEBITO AUTOMATICO ALQUILER": "Vivienda",
    }
    for descripcion, esperada in casos.items():
        resultado = categorize(descripcion, reglas)
        assert resultado is not None, descripcion
        assert resultado[0] == esperada, descripcion


def test_lo_que_no_reconoce_queda_sin_categoria():
    assert categorize("QWERTY SRL 00012345", sort_rules(default_rules())) is None


def test_gana_el_patron_mas_especifico():
    reglas = sort_rules([
        Rule(pattern="coto", category="Supermercado", source="default"),
        Rule(pattern="coto digital", category="Compras online", source="manual"),
    ])
    assert categorize("COMPRA COTO DIGITAL", reglas)[0] == "Compras online"


# --------------------------- importacion ---------------------------

def test_al_importar_se_deduce_la_categoria(auth):
    categorias = _categorias(auth)
    assert categorias["COMPRA COTO DIGITAL"] == "Supermercado"
    assert categorias["YPF SERVICIOS SRL"] == "Transporte"
    assert categorias["NETFLIX.COM"] == "Suscripciones"
    assert categorias["PAGO ALQUILER MARZO"] == "Vivienda"
    # Lo que no reconoce no se inventa.
    assert categorias["QWERTY SRL 00012345"] == "Sin categoria"


def test_la_categoria_del_archivo_manda(auth):
    """Si el archivo trae categoria, las reglas no la pisan."""
    csv = (
        "fecha,concepto,categoria,persona,importe\n"
        "2026-04-01,COMPRA COTO DIGITAL,Mandados,Ana,1000\n"
    )
    auth.post(
        "/archivos/upload",
        files={"file": ("con-categoria.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )
    filas = auth.get("/api/transacciones", params={"categoria": "Mandados"}).json()["rows"]
    assert len(filas) == 1
    assert filas[0]["categoria_origen"] == "file"


# --------------------------- correccion manual ---------------------------

def test_editar_la_categoria_de_un_movimiento(auth):
    fila = next(f for f in auth.get("/api/transacciones").json()["rows"]
                if f["descripcion"] == "QWERTY SRL 00012345")
    response = auth.patch(
        f"/api/transacciones/{fila['id']}",
        json={"categoria": "Ferreteria", "aplicar_a_similares": False},
    )
    assert response.status_code == 200
    assert response.json()["categoria"] == "Ferreteria"
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Ferreteria"


def test_aplicar_a_todos_los_que_digan_lo_mismo(auth):
    csv = "fecha,concepto,persona,importe\n2026-03-20,QWERTY SRL 00012345,Ana,3000\n"
    auth.post(
        "/archivos/upload",
        files={"file": ("mas.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )
    fila = next(f for f in auth.get("/api/transacciones").json()["rows"]
                if f["descripcion"] == "QWERTY SRL 00012345")
    response = auth.patch(
        f"/api/transacciones/{fila['id']}",
        json={"categoria": "Ferreteria", "aplicar_a_similares": True,
              "patron": "QWERTY SRL 00012345"},
    )
    assert response.json()["aplicados"] == 2
    filas = auth.get("/api/transacciones", params={"categoria": "Ferreteria"}).json()["rows"]
    assert len(filas) == 2
    # Y queda como regla, para los archivos que subas despues.
    with SessionLocal() as db:
        reglas = db.execute(select(CategoryRule)).scalars().all()
    assert any(r.pattern == "QWERTY SRL 00012345" and r.category == "Ferreteria" for r in reglas)


def test_recategorizar_no_pisa_lo_corregido_a_mano(auth):
    fila = next(f for f in auth.get("/api/transacciones").json()["rows"]
                if f["descripcion"] == "COMPRA COTO DIGITAL")
    auth.patch(f"/api/transacciones/{fila['id']}",
               json={"categoria": "Regalos", "aplicar_a_similares": False})

    response = auth.post("/categorias/recategorizar", follow_redirects=False)
    assert response.status_code == 303
    # La regla de fabrica diria "Supermercado", pero manda la correccion.
    assert _categorias(auth)["COMPRA COTO DIGITAL"] == "Regalos"


def test_una_regla_nueva_recategoriza_lo_pendiente(auth):
    response = auth.post(
        "/categorias/regla",
        data={"pattern": "QWERTY", "category": "Servicios", "category_new": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Servicios"


def test_regla_con_categoria_nueva(auth):
    auth.post(
        "/categorias/regla",
        data={"pattern": "QWERTY", "category": "Servicios", "category_new": "Proveedores"},
        follow_redirects=False,
    )
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Proveedores"


def test_las_reglas_propias_valen_para_archivos_futuros(auth):
    auth.post("/categorias/regla",
              data={"pattern": "QWERTY", "category": "Servicios", "category_new": "Proveedores"},
              follow_redirects=False)
    csv = "fecha,concepto,persona,importe\n2026-05-01,QWERTY SRL 00099999,Ana,5000\n"
    auth.post(
        "/archivos/upload",
        files={"file": ("nuevo.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )
    assert _categorias(auth)["QWERTY SRL 00099999"] == "Proveedores"


def test_categoria_vacia_da_error(auth):
    fila = auth.get("/api/transacciones").json()["rows"][0]
    response = auth.patch(f"/api/transacciones/{fila['id']}", json={"categoria": "  "})
    assert response.status_code == 400


def test_movimiento_inexistente(auth):
    assert auth.patch("/api/transacciones/99999", json={"categoria": "X"}).status_code == 404


def test_pagina_de_categorias(auth):
    page = auth.get("/categorias")
    assert page.status_code == 200
    # La descripcion que no se pudo categorizar aparece para asignarla.
    assert "QWERTY SRL 00012345" in page.text

"""Categorizacion con el modelo: prioridad, cache y comportamiento ante fallos.

Las llamadas a OpenRouter se sustituyen. Lo que se prueba es la logica
alrededor: a quien se le pregunta, cuantas veces, y que pasa si falla.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import llm
from app.db import SessionLocal
from app.main import app
from app.models import CategoryRule
from app.preferences import CAT_AI, CAT_RULES, set_categorization_mode

from tests.conftest import reset_db

# "QWERTY SRL" no lo reconoce ninguna regla de fabrica; "COTO" si.
CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,QWERTY SRL 00012345,Ana,9500.00\n"
    "2026-03-03,QWERTY SRL 00012345,Ana,3000.00\n"
)


class FakeModel:
    """Sustituye al modelo y anota que descripciones se le preguntaron."""

    def __init__(self, respuestas=None, falla=False):
        self.llamadas: list[list[str]] = []
        self.respuestas = respuestas or {}
        self.falla = falla

    async def __call__(self, descriptions, model=None):
        self.llamadas.append(list(descriptions))
        if self.falla:
            raise llm.OpenRouterError("503 de mentira")
        return {d: self.respuestas.get(d, "Sin categoria") for d in descriptions}

    @property
    def preguntadas(self) -> list[str]:
        return [d for llamada in self.llamadas for d in llamada]


@pytest.fixture
def modelo(monkeypatch):
    fake = FakeModel({"QWERTY SRL 00012345": "Proveedores"})
    # Hay dos sitios que lo importan: el modulo de reglas y el router.
    monkeypatch.setattr(llm, "suggest_categories", fake)
    monkeypatch.setattr("app.routers.categories.suggest_categories", fake)
    monkeypatch.setattr("app.config.Settings.chat_enabled", property(lambda self: True))
    return fake


@pytest.fixture
def auth(modelo):
    reset_db()
    with SessionLocal() as db:
        set_categorization_mode(db, CAT_AI)
    with TestClient(app) as client:
        client.post("/login", data={"username": "tester", "password": "secreto123", "next": "/"},
                    follow_redirects=False)
        yield client


def _subir(client, nombre: str, csv: str):
    return client.post(
        "/archivos/upload",
        files={"file": (nombre, io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )


def _categorias(client) -> dict[str, str]:
    filas = client.get("/api/transacciones").json()["rows"]
    return {f["descripcion"]: f["categoria"] for f in filas}


def test_al_importar_se_le_pregunta_al_modelo(auth, modelo):
    _subir(auth, "extracto.csv", CSV)
    assert "QWERTY SRL 00012345" in modelo.preguntadas
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Proveedores"


def test_se_pregunta_una_vez_por_descripcion_distinta(auth, modelo):
    """Dos movimientos iguales no son dos preguntas."""
    _subir(auth, "extracto.csv", CSV)
    assert modelo.preguntadas.count("QWERTY SRL 00012345") == 1


def test_la_respuesta_queda_cacheada_como_regla(auth, modelo):
    _subir(auth, "extracto.csv", CSV)
    with SessionLocal() as db:
        reglas = db.execute(select(CategoryRule)).scalars().all()
    assert any(r.pattern == "QWERTY SRL 00012345" and r.source == "ai" for r in reglas)


def test_el_segundo_archivo_no_vuelve_a_preguntar(auth, modelo):
    """El punto de guardar la respuesta: no se paga dos veces por lo mismo."""
    _subir(auth, "marzo.csv", CSV)
    preguntas_primera = len(modelo.preguntadas)

    _subir(auth, "abril.csv",
           "fecha,concepto,persona,importe\n2026-04-01,QWERTY SRL 00012345,Ana,7000\n")
    assert len(modelo.preguntadas) == preguntas_primera
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Proveedores"


def test_lo_que_el_modelo_no_sabe_cae_en_las_reglas(auth, modelo):
    """El modelo devuelve 'Sin categoria' para COTO, pero la regla lo sabe."""
    _subir(auth, "extracto.csv", CSV)
    assert _categorias(auth)["COMPRA COTO DIGITAL"] == "Supermercado"


def test_si_el_modelo_falla_la_importacion_no_se_pierde(auth, modelo):
    modelo.falla = True
    response = _subir(auth, "extracto.csv", CSV)
    assert response.status_code == 303

    categorias = _categorias(auth)
    assert len(categorias) == 2  # los movimientos estan guardados
    # Se cayo a las reglas de fabrica para lo que si reconocen.
    assert categorias["COMPRA COTO DIGITAL"] == "Supermercado"
    assert categorias["QWERTY SRL 00012345"] == "Sin categoria"


def test_en_modo_reglas_no_se_consulta_al_modelo(auth, modelo):
    with SessionLocal() as db:
        set_categorization_mode(db, CAT_RULES)
    _subir(auth, "extracto.csv", CSV)
    assert modelo.llamadas == []
    assert _categorias(auth)["COMPRA COTO DIGITAL"] == "Supermercado"


def test_la_correccion_a_mano_le_gana_al_modelo(auth, modelo):
    _subir(auth, "extracto.csv", CSV)
    fila = next(f for f in auth.get("/api/transacciones").json()["rows"]
                if f["descripcion"] == "QWERTY SRL 00012345")
    auth.patch(f"/api/transacciones/{fila['id']}",
               json={"categoria": "Ferreteria", "aplicar_a_similares": False})

    auth.post("/categorias/recategorizar", follow_redirects=False)
    filas = auth.get("/api/transacciones").json()["rows"]
    corregida = next(f for f in filas if f["id"] == fila["id"])
    assert corregida["categoria"] == "Ferreteria"


def test_el_boton_de_sugerir_sigue_funcionando(auth, modelo):
    with SessionLocal() as db:
        set_categorization_mode(db, CAT_RULES)
    _subir(auth, "extracto.csv", CSV)
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Sin categoria"

    response = auth.post("/categorias/sugerir", follow_redirects=False)
    assert response.status_code == 303
    assert _categorias(auth)["QWERTY SRL 00012345"] == "Proveedores"

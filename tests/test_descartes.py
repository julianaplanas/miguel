"""El modelo decide que lineas no son movimientos, y se cachea como regla.

Que una linea sea un total, un saldo o un arrastre no se puede enumerar de
antemano: cada banco lo escribe distinto. Se le pregunta al modelo una vez
por descripcion y la respuesta queda guardada.
"""
from __future__ import annotations

import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import llm
from app.categorize import NOT_A_MOVEMENT
from app.db import SessionLocal
from app.main import app
from app.models import CategoryRule, Transaction
from app.preferences import CAT_AI, set_categorization_mode

from tests.conftest import reset_db

# Un extracto con dos lineas que no son gastos, escritas como las escribiria
# un banco cualquiera: ninguna lista de palabras las tiene todas.
CSV = (
    "fecha,concepto,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Ana,48200.50\n"
    "2026-03-02,ARRASTRE EJERCICIO ANTERIOR,Ana,1250000.00\n"
    "2026-03-03,QWERTY SRL 00012345,Ana,9500.00\n"
    "2026-03-31,SUMATORIA MOVIMIENTOS PERIODO,Ana,1307700.50\n"
)

RESPUESTAS = {
    "COMPRA COTO DIGITAL": "Supermercado",
    "QWERTY SRL 00012345": "Proveedores",
    "ARRASTRE EJERCICIO ANTERIOR": "No es un movimiento",
    "SUMATORIA MOVIMIENTOS PERIODO": "No es un movimiento",
}


class FakeModel:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas: list[list[str]] = []

    async def __call__(self, descriptions, model=None):
        textos = [d["descripcion"] if isinstance(d, dict) else d for d in descriptions]
        self.llamadas.append(textos)
        return {t: self.respuestas.get(t, "Sin categoria") for t in textos}

    @property
    def preguntadas(self) -> list[str]:
        return [d for llamada in self.llamadas for d in llamada]


@pytest.fixture
def modelo(monkeypatch):
    fake = FakeModel(RESPUESTAS)
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
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        yield client


def _subir(client, nombre="extracto.csv", csv=CSV):
    return client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(csv.encode("utf-8")), "text/csv")},
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )


def _descripciones(client) -> set[str]:
    return {f["descripcion"] for f in client.get("/api/transacciones").json()["rows"]}


def test_las_lineas_que_no_son_movimientos_se_descartan(auth):
    _subir(auth)
    assert _descripciones(auth) == {"COMPRA COTO DIGITAL", "QWERTY SRL 00012345"}
    # Y no inflan el total: 48.200,50 + 9.500, sin el arrastre ni la suma.
    assert auth.get("/api/resumen").json()["kpis"]["total_expense"] == pytest.approx(57700.50)


def test_la_decision_queda_cacheada_como_regla(auth):
    _subir(auth)
    with SessionLocal() as db:
        reglas = {r.pattern: r.category for r in db.execute(select(CategoryRule)).scalars()}
    assert reglas["ARRASTRE EJERCICIO ANTERIOR"] == NOT_A_MOVEMENT
    assert reglas["SUMATORIA MOVIMIENTOS PERIODO"] == NOT_A_MOVEMENT


def test_al_reimportar_no_vuelven_a_entrar_ni_se_vuelve_a_preguntar(auth, modelo):
    _subir(auth)
    preguntas_primera = list(modelo.preguntadas)
    _subir(auth, "otro-mes.csv", CSV)

    assert _descripciones(auth) == {"COMPRA COTO DIGITAL", "QWERTY SRL 00012345"}
    # La segunda subida no le pregunta nada al modelo: ya estaba decidido.
    assert modelo.preguntadas == preguntas_primera


def test_el_aviso_dice_cuantas_se_descartaron(auth):
    destino = unquote(_subir(auth).headers["location"])
    assert "descartaron 2 lineas" in destino


def test_el_archivo_cuenta_solo_lo_que_quedo(auth):
    _subir(auth)
    assert "2 movimientos" in auth.get("/archivos").text


def test_si_el_modelo_se_equivoca_mandas_vos(auth, modelo):
    """Una regla a mano gana: la linea vuelve y no se le vuelve a preguntar.

    Es la salida cuando el modelo descarta algo que si era un gasto. Borrar
    la regla del modelo no alcanza, porque al reimportar contestaria lo
    mismo; hay que decirle que categoria va.
    """
    _subir(auth)
    assert "ARRASTRE EJERCICIO ANTERIOR" not in _descripciones(auth)

    auth.post(
        "/categorias/regla",
        data={"pattern": "ARRASTRE EJERCICIO ANTERIOR", "category": "", "category_new": "Otros"},
        follow_redirects=False,
    )
    auth.post("/archivos/reprocesar", follow_redirects=False)

    filas = {f["descripcion"]: f for f in auth.get("/api/transacciones").json()["rows"]}
    assert filas["ARRASTRE EJERCICIO ANTERIOR"]["categoria"] == "Otros"
    # Y la otra linea de totales sigue descartada.
    assert "SUMATORIA MOVIMIENTOS PERIODO" not in filas


# --- Repaso completo, a pedido -------------------------------------------
#
# El paso de la importacion solo pregunta por lo que quedo sin categoria.
# Una linea de totales que YA tiene categoria (de una regla vieja, o de
# antes de que esto existiera) no se revisa sola nunca.

CSV_YA_CATEGORIZADO = (
    "fecha,concepto,categoria,persona,importe\n"
    "2026-03-01,COMPRA COTO DIGITAL,Supermercado,Ana,48200.50\n"
    "2026-03-31,SUMATORIA MOVIMIENTOS PERIODO,Transferencias,Ana,1307700.50\n"
)


class FakeRevisor:
    """Sustituye a find_non_movements y anota que se le pregunto."""

    def __init__(self, descartar, inventar=()):
        self.descartar = set(descartar)
        self.inventar = list(inventar)
        self.preguntadas: list[str] = []

    async def __call__(self, descriptions, model=None):
        textos = [d["descripcion"] for d in descriptions]
        self.preguntadas.extend(textos)
        return [t for t in textos if t in self.descartar] + self.inventar


def test_el_repaso_encuentra_lo_que_ya_tenia_categoria(auth, monkeypatch):
    _subir(auth, "ya-categorizado.csv", CSV_YA_CATEGORIZADO)
    assert "SUMATORIA MOVIMIENTOS PERIODO" in _descripciones(auth)

    revisor = FakeRevisor(["SUMATORIA MOVIMIENTOS PERIODO"])
    monkeypatch.setattr("app.llm.find_non_movements", revisor)

    respuesta = auth.post("/categorias/revisar-lineas", follow_redirects=False)
    assert respuesta.status_code == 303
    assert _descripciones(auth) == {"COMPRA COTO DIGITAL"}
    # Se le pregunto por las dos, no solo por las que no tenian categoria.
    assert set(revisor.preguntadas) == {"COMPRA COTO DIGITAL", "SUMATORIA MOVIMIENTOS PERIODO"}


def test_el_repaso_no_toca_lo_corregido_a_mano(auth, monkeypatch):
    _subir(auth, "ya-categorizado.csv", CSV_YA_CATEGORIZADO)
    filas = {f["descripcion"]: f["id"] for f in auth.get("/api/transacciones").json()["rows"]}
    auth.patch(
        f"/api/transacciones/{filas['SUMATORIA MOVIMIENTOS PERIODO']}",
        json={"categoria": "Ingresos", "aplicar_a_similares": False},
    )

    revisor = FakeRevisor(["SUMATORIA MOVIMIENTOS PERIODO"])
    monkeypatch.setattr("app.llm.find_non_movements", revisor)
    auth.post("/categorias/revisar-lineas", follow_redirects=False)

    assert "SUMATORIA MOVIMIENTOS PERIODO" not in revisor.preguntadas
    assert "SUMATORIA MOVIMIENTOS PERIODO" in _descripciones(auth)


def test_el_repaso_solo_borra_lo_que_mando(auth, monkeypatch):
    """Si el modelo devuelve algo que no estaba en la lista, se ignora."""
    _subir(auth, "ya-categorizado.csv", CSV_YA_CATEGORIZADO)
    revisor = FakeRevisor([], inventar=["COMPRA COTO", "cualquier cosa"])
    monkeypatch.setattr("app.llm.find_non_movements", revisor)

    respuesta = auth.post("/categorias/revisar-lineas", follow_redirects=False)
    assert "no hay lineas que sobren" in unquote(respuesta.headers["location"])
    assert "COMPRA COTO DIGITAL" in _descripciones(auth)

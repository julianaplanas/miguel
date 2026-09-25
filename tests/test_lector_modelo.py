"""Leer el PDF con el modelo cuando el lector automatico no cuadra.

Lo que decide si hace falta el modelo no es una opinion: es la suma de lo
leido contra los totales que declara el propio resumen.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import pdf_llm
from app.db import SessionLocal
from app.main import app
from app.models import UploadedFile
from app.preferences import PDF_ALWAYS, PDF_AUTO, PDF_FALLBACK, set_pdf_reader_mode

from tests.conftest import reset_db
from tests.pdf_fixtures import resumen_dos_titulares, resumen_tarjeta_dos_columnas

# Lo que "leeria" el modelo: las mismas 7 filas del resumen de dos
# titulares, mas los totales que el documento declara.
LECTURA = {
    "movimientos": [
        {"fecha": "2026-08-02", "descripcion": "MERPAGO*LIBRERIA", "importe": "12.500,00", "moneda": ""},
        {"fecha": "2026-08-05", "descripcion": "OPENAI *CHATGPT", "importe": "20,00", "moneda": "USD"},
        {"fecha": "2026-08-11", "descripcion": "SUBTE SUBE", "importe": "8.000,00", "moneda": ""},
        {"fecha": "2026-08-03", "descripcion": "FARMACITY", "importe": "35.400,50", "moneda": ""},
        {"fecha": "2026-08-09", "descripcion": "NETFLIX.COM", "importe": "12,99", "moneda": "USD"},
        {"fecha": "2026-08-15", "descripcion": "COTO CICSA", "importe": "89.100,00", "moneda": ""},
        {"fecha": "2026-08-21", "descripcion": "YPF FULL", "importe": "47.000,00", "moneda": ""},
    ],
    "totales": [
        {"descripcion": "Total a pagar", "importe": "192.000,50", "moneda": ""},
    ],
    "paginas": 2,
    "errores": [],
}


class FakeLector:
    """Sustituye la llamada al modelo y anota cuantas veces se uso."""

    def __init__(self, respuesta=None):
        self.respuesta = respuesta if respuesta is not None else LECTURA
        self.llamadas = 0

    async def __call__(self, data, moneda_base, model=None):
        self.llamadas += 1
        return self.respuesta


@pytest.fixture
def lector(monkeypatch):
    fake = FakeLector()
    monkeypatch.setattr("app.routers.files.extract_pages", fake)
    monkeypatch.setattr("app.config.Settings.chat_enabled", property(lambda self: True))
    return fake


@pytest.fixture
def auth(lector):
    reset_db()
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        yield client


def _subir(client, nombre, contenido):
    return client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(contenido), "application/pdf")},
        data={"default_person": "", "default_currency": "ARS", "revisar": "1"},
        follow_redirects=False,
    )


def _ultimo() -> UploadedFile:
    with SessionLocal() as db:
        return db.execute(select(UploadedFile).order_by(UploadedFile.id.desc())).scalars().first()


def test_si_la_lectura_automatica_cuadra_no_se_llama_al_modelo(auth, lector):
    """El resumen de dos columnas cuadra al centavo: no hay nada que mejorar."""
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_FALLBACK)
    _subir(auth, "master.pdf", resumen_tarjeta_dos_columnas())

    assert lector.llamadas == 0
    assert _ultimo().reader == ""


def test_si_no_cuadra_lo_lee_el_modelo(auth, lector, monkeypatch):
    """Se fuerza una lectura automatica incompleta y el modelo la reemplaza."""
    import app.routers.files as files

    # La primera comprobacion es la del lector automatico (no cuadra); la
    # segunda, la de lo que leyo el modelo (si).
    llamadas = {"n": 0}

    def cuadra(db, parsed, moneda):
        llamadas["n"] += 1
        return llamadas["n"] > 1

    monkeypatch.setattr(files, "_cuadra", cuadra)
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_FALLBACK)
    _subir(auth, "titulares.pdf", resumen_dos_titulares())

    assert lector.llamadas == 1
    record = _ultimo()
    assert record.reader == "modelo"
    assert json.loads(record.ai_rows)["movimientos"]

    # Y lo que se ve en la revision son las filas del modelo.
    pagina = auth.get(f"/archivos/{record.id}/revisar").text
    assert "Leido por <strong>el modelo</strong>" in pagina
    assert "COTO CICSA" in pagina
    assert "coincide al centavo" in pagina


def test_en_modo_automatico_nunca_se_llama_al_modelo(auth, lector, monkeypatch):
    import app.routers.files as files

    monkeypatch.setattr(files, "_cuadra", lambda db, parsed, moneda: False)
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_AUTO)
    _subir(auth, "titulares.pdf", resumen_dos_titulares())
    assert lector.llamadas == 0


def test_en_modo_siempre_lo_lee_el_modelo_aunque_cuadre(auth, lector):
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_ALWAYS)
    _subir(auth, "master.pdf", resumen_tarjeta_dos_columnas())
    assert lector.llamadas == 1
    assert _ultimo().reader == "modelo"


def test_las_filas_del_modelo_pasan_por_el_mismo_molino(auth, lector):
    """Importes, fechas y monedas se normalizan igual que las de un CSV."""
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_ALWAYS)
    _subir(auth, "titulares.pdf", resumen_dos_titulares())
    record = _ultimo()

    auth.post(
        f"/archivos/{record.id}/revisar",
        data={"accion": "importar", "default_currency": "ARS"},
        follow_redirects=False,
    )
    filas = auth.get("/api/transacciones").json()["rows"]
    assert len(filas) == 7
    por_desc = {f["descripcion"]: f for f in filas}
    assert por_desc["COTO CICSA"]["importe"] == 89100.00
    assert por_desc["COTO CICSA"]["moneda"] == "ARS"
    assert por_desc["NETFLIX.COM"]["moneda"] == "USD"
    assert por_desc["NETFLIX.COM"]["fecha"] == "2026-08-09"


def test_si_el_modelo_falla_se_queda_el_lector_automatico(auth, monkeypatch):
    import app.routers.files as files

    fallo = FakeLector({"movimientos": [], "totales": [], "paginas": 0, "errores": ["503"]})
    monkeypatch.setattr(files, "extract_pages", fallo)
    monkeypatch.setattr(files, "_cuadra", lambda db, parsed, moneda: False)
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_FALLBACK)
    respuesta = _subir(auth, "titulares.pdf", resumen_dos_titulares())

    assert respuesta.status_code == 303
    record = _ultimo()
    assert record.reader == ""
    # La lectura automatica sigue estando: 7 filas, no cero.
    assert record.row_count == 7


def test_se_puede_volver_al_lector_automatico(auth, lector):
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_ALWAYS)
    _subir(auth, "titulares.pdf", resumen_dos_titulares())
    record = _ultimo()
    assert record.reader == "modelo"

    auth.post(
        f"/archivos/{record.id}/revisar",
        data={"accion": "lector-automatico", "default_currency": "ARS"},
        follow_redirects=False,
    )
    assert _ultimo().reader == ""


def test_no_se_gasta_una_llamada_por_cada_vista_previa(auth, lector):
    """La lectura del modelo queda guardada en el archivo."""
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_ALWAYS)
    _subir(auth, "titulares.pdf", resumen_dos_titulares())
    record = _ultimo()
    auth.get(f"/archivos/{record.id}/revisar")
    auth.get(f"/archivos/{record.id}/revisar")
    assert lector.llamadas == 1


def test_solo_se_le_mandan_las_hojas_con_importes():
    """Las hojas de condiciones generales no gastan una llamada."""
    textos = pdf_llm.page_texts(resumen_tarjeta_dos_columnas())
    assert any(pdf_llm.worth_reading(t) for t in textos)
    assert not pdf_llm.worth_reading("CONDICIONES GENERALES\nEl titular se obliga a...")


def test_el_modelo_no_gana_por_ser_el_modelo(auth, monkeypatch):
    """Si su lectura no cuadra y trae menos filas, se queda la automatica."""
    import app.routers.files as files

    pobre = FakeLector(
        {
            "movimientos": [
                {"fecha": "2026-08-02", "descripcion": "ALGO", "importe": "1,00", "moneda": ""}
            ],
            "totales": [],
            "paginas": 1,
            "errores": [],
        }
    )
    monkeypatch.setattr(files, "extract_pages", pobre)
    monkeypatch.setattr(files, "_cuadra", lambda db, parsed, moneda: False)
    with SessionLocal() as db:
        set_pdf_reader_mode(db, PDF_FALLBACK)
    respuesta = _subir(auth, "titulares.pdf", resumen_dos_titulares())

    assert pobre.llamadas == 1
    record = _ultimo()
    assert record.reader == ""
    assert record.row_count == 7
    from urllib.parse import unquote

    assert "no mejoro" in unquote(respuesta.headers["location"])

"""El banco y la tarjeta de cada archivo salen de su cabecera, no de adivinar."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.chat_tools import agrupar
from app.db import SessionLocal
from app.llm import build_data_context
from app.main import app
from app.models import UploadedFile
from app.origen import detectar, etiqueta

from tests.conftest import reset_db


def test_detectar_en_cabecera():
    assert detectar("BANCO GALICIA\nResumen de cuenta VISA\nCierre 12/08/2026") == {
        "banco": "Galicia", "tarjeta": "Visa",
    }
    assert detectar("Santander Río S.A.  Tarjeta MASTERCARD Gold") == {
        "banco": "Santander", "tarjeta": "Mastercard",
    }
    assert detectar("American Express Argentina S.A.\nResumen de cuenta") == {
        "banco": "", "tarjeta": "American Express",
    }
    assert detectar("BBVA Argentina - Extracto de cuenta corriente") == {"banco": "BBVA", "tarjeta": ""}


def test_la_cabecera_manda_sobre_los_movimientos_y_el_nombre():
    # Un extracto del Galicia con un "PAGO VISA SANTANDER" dentro sigue
    # siendo del Galicia y no es un resumen de tarjeta.
    texto = "Banco Galicia - Extracto\n01/03 PAGO TARJETA VISA SANTANDER -215.400,75"
    assert detectar(texto, "santander.pdf")["banco"] == "Galicia"
    # Sin cabecera, el nombre del archivo es la pista.
    assert detectar("", "resumen_macro_amex_agosto.pdf") == {"banco": "Macro", "tarjeta": "American Express"}
    assert detectar("", "gastos (3).csv") == {"banco": "", "tarjeta": ""}


def test_etiqueta():
    assert etiqueta("Galicia", "Visa") == "Galicia Visa"
    assert etiqueta("", "Visa") == "Visa"
    assert etiqueta("", "") == ""


def _pdf(cabecera: str, comercio: str = "COTO DIGITAL") -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    y = 280 * mm
    for linea in cabecera.splitlines():
        c.drawString(20 * mm, y, linea)
        y -= 6 * mm
    y -= 6 * mm
    c.drawString(20 * mm, y, "FECHA       COMERCIO                          IMPORTE")
    y -= 6 * mm
    for fecha, comercio, importe in (
        ("04/02/2026", comercio, "$ 18.400,00"),
        ("12/02/2026", comercio + " 2", "$ 45.000,00"),
    ):
        c.drawString(20 * mm, y, f"{fecha}  {comercio:<35}{importe:>15}")
        y -= 5.5 * mm
    c.showPage()
    c.save()
    return buffer.getvalue()


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


def test_al_subir_se_guarda_el_origen_y_el_chat_puede_agrupar_por_el(auth):
    auth.post(
        "/archivos/upload",
        files={"files": ("resumen (1).pdf", io.BytesIO(_pdf("BANCO GALICIA S.A.\nResumen de cuenta VISA")), "application/pdf")},
        data={"revisar": ""},
        follow_redirects=False,
    )
    auth.post(
        "/archivos/upload",
        files={"files": ("resumen (2).pdf", io.BytesIO(_pdf("Santander Rio\nTarjeta Mastercard", "FARMACITY")), "application/pdf")},
        data={"revisar": ""},
        follow_redirects=False,
    )
    with SessionLocal() as db:
        archivos = {f.filename: (f.bank, f.card) for f in db.execute(select(UploadedFile)).scalars()}
        assert archivos == {"resumen (1).pdf": ("Galicia", "Visa"), "resumen (2).pdf": ("Santander", "Mastercard")}
        por_banco = agrupar(db, {"por": "banco"})
        por_origen = agrupar(db, {"por": "origen"})
        contexto = build_data_context(db)
    assert {g["banco"]: g["gasto"] for g in por_banco["grupos"]} == {"Galicia": 63400.0, "Santander": 63400.0}
    assert {g["origen"] for g in por_origen["grupos"]} == {"Galicia Visa", "Santander Mastercard"}
    assert {o["nombre"] for o in contexto["por_origen"]} == {"Galicia Visa", "Santander Mastercard"}
    assert contexto["por_archivo"][0]["origen"] in {"Galicia Visa", "Santander Mastercard"}

    # En la lista de archivos se ve de que es cada uno.
    html = auth.get("/archivos").text
    assert "Galicia Visa" in html and "Santander Mastercard" in html


def test_el_origen_se_corrige_en_la_revision(auth):
    auth.post(
        "/archivos/upload",
        files={"files": ("x.pdf", io.BytesIO(_pdf("Resumen de cuenta")), "application/pdf")},
        data={"revisar": "1"},
        follow_redirects=False,
    )
    with SessionLocal() as db:
        record = db.execute(select(UploadedFile)).scalar_one()
        assert (record.bank, record.card) == ("", "")
        file_id = record.id
    pagina = auth.get(f"/archivos/{file_id}/revisar").text
    assert 'name="banco"' in pagina and 'name="tarjeta"' in pagina

    auth.post(
        f"/archivos/{file_id}/revisar",
        data={"accion": "importar", "banco": "ICBC", "tarjeta": "Visa", "default_currency": "ARS"},
        follow_redirects=False,
    )
    with SessionLocal() as db:
        record = db.get(UploadedFile, file_id)
        assert (record.bank, record.card, record.imported) == ("ICBC", "Visa", True)


def test_el_dashboard_filtra_por_origen(auth):
    auth.post(
        "/archivos/upload",
        files={"files": ("g.pdf", io.BytesIO(_pdf("BANCO GALICIA\nResumen VISA")), "application/pdf")},
        data={"revisar": ""},
        follow_redirects=False,
    )
    auth.post(
        "/archivos/upload",
        files={"files": ("s.pdf", io.BytesIO(_pdf("Santander Rio\nMastercard", "FARMACITY")), "application/pdf")},
        data={"revisar": ""},
        follow_redirects=False,
    )
    todo = auth.get("/api/resumen").json()
    galicia = auth.get("/api/resumen", params={"origen": "Galicia Visa"}).json()
    assert todo["kpis"]["transactions"] == 4
    assert galicia["kpis"]["transactions"] == 2
    assert galicia["kpis"]["total_expense"] == pytest.approx(63400.0)
    html = auth.get("/").text
    assert 'id="origen"' in html and "Santander Mastercard" in html
    assert todo["options"]["sources"] == ["Galicia Visa", "Santander Mastercard"]

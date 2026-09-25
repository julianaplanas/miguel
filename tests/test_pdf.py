"""Lectura de resumenes bancarios en PDF."""
from __future__ import annotations

import datetime as dt
import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.ingest import parse_file
from app.main import app
from app.pdf_import import PdfImportError, extract_rows

from tests.conftest import reset_db
from tests.pdf_fixtures import (
    extracto_con_saldo,
    pdf_escaneado,
    resumen_tarjeta_dolares,
    resumen_tarjeta_dos_columnas,
)


@pytest.fixture(scope="module")
def auth():
    reset_db()
    with TestClient(app) as client:
        client.post("/login", data={"username": "tester", "password": "secreto123", "next": "/"},
                    follow_redirects=False)
        yield client


def test_extracto_bancario(tmp_path):
    df = extract_rows(extracto_con_saldo())
    assert list(df.columns) == ["fecha", "descripcion", "importe", "moneda"]
    # 6 lineas en el PDF, pero "SALDO ANTERIOR" no es un movimiento.
    assert len(df) == 5
    assert "SALDO ANTERIOR" not in set(df["descripcion"])
    assert df.iloc[0]["fecha"] == "2026-03-03"
    assert "COTO" in df.iloc[0]["descripcion"]
    # Se toma el importe del movimiento, no el saldo acumulado que va al lado.
    assert df.iloc[0]["importe"] == "-48.200,50"


def test_el_ano_se_saca_del_documento():
    """Las fechas vienen como 03/03 y el ano solo aparece en la cabecera."""
    df = extract_rows(extracto_con_saldo())
    assert all(f.startswith("2026-") for f in df["fecha"])


def test_dolares_en_resumen_de_tarjeta():
    df = extract_rows(resumen_tarjeta_dolares())
    monedas = dict(zip(df["descripcion"], df["moneda"]))
    assert monedas["NETFLIX.COM"] == "USD"
    assert monedas["AWS AMAZON WEB SERVICES"] == "USD"
    # `$` a secas no se marca: lo resuelve la moneda del archivo.
    assert monedas["YPF SERVICIOS"] == ""


def test_resumen_con_columnas_de_pesos_y_dolares():
    """El importe se toma de la columna donde cae, no del orden del texto."""
    df = extract_rows(resumen_tarjeta_dos_columnas())
    filas = {r["descripcion"]: r for _, r in df.iterrows()}

    # Cae en la columna DOLARES aunque en la descripcion diga ARS: lo que
    # manda es donde esta escrito el importe.
    assert filas["Spotify (SWE,ARS, 5499,00)"]["moneda"] == "USD"
    assert filas["Spotify (SWE,ARS, 5499,00)"]["importe"] == "3,67"
    assert filas["APPLE.COM/BILL (USA,USD, 0,99)"]["moneda"] == "USD"
    # Columna PESOS: la moneda la pone el usuario al subir el archivo.
    assert filas["WWW1.HOSPITALITALIANO"]["moneda"] == ""
    assert filas["WWW1.HOSPITALITALIANO"]["importe"] == "21.425,71"

    # Ni el pago del resumen anterior ni los totales son movimientos, ni
    # siquiera el que lleva fecha y parece uno mas.
    assert len(df) == 7
    assert not any("SU PAGO" in d for d in df["descripcion"])
    assert not any("TOTAL A PAGAR" in d for d in df["descripcion"])
    assert not any("TOTAL DEL MES" in d for d in df["descripcion"])
    # Pero un comercio que empieza con esas letras no es una linea de total.
    assert "TOTALGAS SRL" in set(df["descripcion"])
    # El numero de comprobante cambia en cada linea: si quedara pegado a la
    # descripcion, ningun comercio se repetiria y cada uno costaria una
    # consulta al modelo.
    assert not any("08783" in d for d in df["descripcion"])


def test_las_hojas_siguientes_no_repiten_la_cabecera():
    """La segunda hoja no trae la fila PESOS/DOLARES y se lee igual.

    Buscando las columnas hoja por hoja, esas paginas se perdian enteras:
    el total quedaba corto y nada lo avisaba.
    """
    df = extract_rows(resumen_tarjeta_dos_columnas())
    filas = {r["descripcion"]: r for _, r in df.iterrows()}
    assert filas["AWS AMAZON WEB SERVICES"]["moneda"] == "USD"
    assert filas["AWS AMAZON WEB SERVICES"]["importe"] == "42,30"
    assert filas["TOTALGAS SRL"]["moneda"] == ""


def test_fechas_con_el_mes_en_letras():
    """29-Jul-26, como las imprimen los resumenes de tarjeta."""
    fechas = set(extract_rows(resumen_tarjeta_dos_columnas())["fecha"])
    assert "2026-07-29" in fechas
    assert "2026-08-10" in fechas
    # Una cuota lleva la fecha de la compra original, de otro ano.
    assert "2025-09-12" in fechas


def test_pdf_escaneado_da_un_error_claro():
    with pytest.raises(PdfImportError) as exc:
        extract_rows(pdf_escaneado())
    assert "escaneado" in str(exc.value)


def test_pdf_que_no_es_un_pdf():
    with pytest.raises(PdfImportError):
        extract_rows(b"esto no es un pdf")


def test_ingesta_completa_desde_pdf(tmp_path):
    """El PDF pasa por el mismo camino que un CSV: mapeo y normalizacion."""
    path = tmp_path / "extracto.pdf"
    path.write_bytes(extracto_con_saldo())
    parsed = parse_file(path, default_person="Miguel", default_currency="ARS")

    assert parsed.mapping["date"] == "fecha"
    assert parsed.mapping["amount"] == "importe"
    # Los gastos vienen en negativo: se invierte el signo.
    assert parsed.mapping["invert_sign"] is True

    por_fecha = {r["date"]: r for r in parsed.rows}
    compra = por_fecha[dt.date(2026, 3, 3)]
    assert compra["amount"] == 48200.50   # gasto en positivo
    assert compra["person"] == "Miguel"
    assert compra["currency"] == "ARS"
    # El sueldo es un ingreso: queda en negativo.
    assert por_fecha[dt.date(2026, 3, 10)]["amount"] == -1850000.00


def test_subir_pdf_por_la_ui(auth):
    response = auth.post(
        "/archivos/upload",
        files={"files": ("extracto.pdf", io.BytesIO(extracto_con_saldo()), "application/pdf")},
        data={"default_person": "Ana", "default_currency": "ARS"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" not in response.headers["location"]

    data = auth.get("/api/resumen").json()
    assert data["kpis"]["transactions"] == 5
    # 48.200,50 + 32.150 + 480.000 + 215.400,75
    assert data["kpis"]["total_expense"] == pytest.approx(775751.25)
    assert data["kpis"]["total_income"] == pytest.approx(1850000.00)


def test_pdf_de_tarjeta_mezcla_monedas(auth):
    response = auth.post(
        "/archivos/upload",
        files={"files": ("tarjeta.pdf", io.BytesIO(resumen_tarjeta_dolares()), "application/pdf")},
        data={"default_person": "Ana", "default_currency": "ARS"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    opciones = auth.get("/api/opciones").json()
    assert set(opciones["currencies"]) == {"ARS", "USD"}


def test_formato_no_soportado(auth):
    response = auth.post(
        "/archivos/upload",
        files={"files": ("foto.png", io.BytesIO(b"x"), "image/png")},
        data={"default_person": "", "default_currency": "ARS"},
        follow_redirects=False,
    )
    # La subida viene de un formulario: el error vuelve en el redirect, no
    # como pagina de error, para que se vea junto al formulario.
    assert response.status_code == 303
    destino = unquote(response.headers["location"])
    assert "error=" in destino
    assert "foto.png" in destino
    assert "PDF" in destino


def test_subir_resumen_de_dos_columnas_por_la_ui(auth):
    response = auth.post(
        "/archivos/upload",
        files={"files": ("master.pdf", io.BytesIO(resumen_tarjeta_dos_columnas()), "application/pdf")},
        data={"default_person": "Ana", "default_currency": "ARS"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" not in response.headers["location"]
    opciones = auth.get("/api/opciones").json()
    assert set(opciones["currencies"]) == {"ARS", "USD"}

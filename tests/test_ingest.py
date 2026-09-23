"""Pruebas de la lectura y normalizacion de archivos."""
from __future__ import annotations

import datetime as dt

import pytest

from app.ingest import detect_mapping, parse_amount, parse_file


def test_parse_amount_formatos():
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("1,234.56") == 1234.56
    assert parse_amount("-12,50 EUR") == -12.5
    assert parse_amount("(89.90)") == -89.9
    assert parse_amount("45") == 45.0
    assert parse_amount("") is None
    assert parse_amount(None) is None
    assert parse_amount("sin importe") is None


def test_detect_mapping_espanol():
    mapping = detect_mapping(["Fecha", "Concepto", "Categoría", "Persona", "Importe (EUR)"])
    assert mapping["date"] == "Fecha"
    assert mapping["amount"] == "Importe (EUR)"
    assert mapping["category"] == "Categoría"
    assert mapping["person"] == "Persona"
    assert mapping["description"] == "Concepto"


def test_parse_csv_positivo(tmp_path):
    path = tmp_path / "gastos.csv"
    path.write_text(
        "Fecha;Concepto;Categoría;Persona;Importe\n"
        "01/03/2026;Supermercado;Comida;Ana;45,20\n"
        "02/03/2026;Cine;Ocio;Miguel;18,00\n",
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.row_count == 2
    assert parsed.rows[0]["date"] == dt.date(2026, 3, 1)
    assert parsed.rows[0]["amount"] == 45.20
    assert parsed.rows[0]["person"] == "Ana"
    assert parsed.rows[1]["category"] == "Ocio"
    # Todo positivo -> no se invierte el signo.
    assert parsed.mapping["invert_sign"] is False


def test_parse_csv_extracto_bancario_invierte_signo(tmp_path):
    path = tmp_path / "banco.csv"
    path.write_text(
        "fecha,descripcion,importe\n"
        "2026-01-05,Compra super,-45.20\n"
        "2026-01-06,Restaurante,-30.00\n"
        "2026-01-31,Nomina,2000.00\n",
        encoding="utf-8",
    )
    parsed = parse_file(path, default_person="Miguel")
    assert parsed.mapping["invert_sign"] is True
    # Gastos quedan en positivo, el ingreso en negativo.
    assert parsed.rows[0]["amount"] == 45.20
    assert parsed.rows[2]["amount"] == -2000.00
    assert parsed.rows[0]["person"] == "Miguel"


def test_parse_excel(tmp_path):
    pd = pytest.importorskip("pandas")
    path = tmp_path / "gastos.xlsx"
    pd.DataFrame(
        {
            "Fecha": ["2026-02-01", "2026-02-03"],
            "Descripcion": ["Luz", "Agua"],
            "Categoria": ["Hogar", "Hogar"],
            "Quien": ["Ana", "Ana"],
            "Monto": [60.5, 22.0],
        }
    ).to_excel(path, index=False)
    parsed = parse_file(path)
    assert parsed.row_count == 2
    assert parsed.rows[0]["category"] == "Hogar"
    assert parsed.rows[0]["amount"] == 60.5


def test_columna_tipo_ingreso_gasto(tmp_path):
    path = tmp_path / "mixto.csv"
    path.write_text(
        "fecha,concepto,tipo,importe\n"
        "2026-04-01,Sueldo,Ingreso,1500\n"
        "2026-04-02,Alquiler,Gasto,800\n",
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.rows[0]["amount"] == -1500.0
    assert parsed.rows[1]["amount"] == 800.0


def test_archivo_sin_importes_falla(tmp_path):
    path = tmp_path / "raro.csv"
    path.write_text("a,b\nhola,mundo\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_file(path)


def test_unos_pocos_ingresos_no_dan_vuelta_la_planilla(tmp_path):
    """Gastos en positivo con un ingreso suelto: no hay que invertir nada."""
    path = tmp_path / "gastos.csv"
    path.write_text(
        "fecha,concepto,persona,importe\n"
        "2026-03-08,Kiosco,Ana,3500\n"
        "2026-03-09,Libreria,Ana,8000\n"
        "2026-03-10,Devolucion,Ana,-5000\n",
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.mapping["invert_sign"] is False
    importes = {r["description"]: r["amount"] for r in parsed.rows}
    assert importes["Kiosco"] == 3500.0        # sigue siendo gasto
    assert importes["Devolucion"] == -5000.0   # sigue siendo ingreso


def test_extracto_con_mayoria_negativa_si_se_invierte(tmp_path):
    path = tmp_path / "banco.csv"
    path.write_text(
        "fecha,concepto,persona,importe\n"
        "2026-03-01,Super,Ana,-12000\n"
        "2026-03-02,Nafta,Ana,-30000\n"
        "2026-03-03,Farmacia,Ana,-5000\n"
        "2026-03-10,Sueldo,Ana,900000\n",
        encoding="utf-8",
    )
    parsed = parse_file(path)
    assert parsed.mapping["invert_sign"] is True
    importes = {r["description"]: r["amount"] for r in parsed.rows}
    assert importes["Super"] == 12000.0     # gasto en positivo
    assert importes["Sueldo"] == -900000.0  # ingreso en negativo

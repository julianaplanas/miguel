"""Deteccion de moneda y agregaciones multi-moneda."""
from __future__ import annotations

import pytest

from app import currency as cur
from app.ingest import parse_file


def test_dolar_vs_peso_por_simbolo():
    # En Argentina `$` es pesos; hay que resolverlo con la moneda por defecto.
    assert cur.detect_in_amount("$ 1.500,00", "ARS") == "ARS"
    assert cur.detect_in_amount("US$ 120,50", "ARS") == "USD"
    assert cur.detect_in_amount("U$S 120,50", "ARS") == "USD"
    assert cur.detect_in_amount("1.500,00", "ARS") == "ARS"
    assert cur.detect_in_amount("€ 40", "ARS") == "EUR"


def test_normalizar_codigos():
    assert cur.normalize_code("usd") == "USD"
    assert cur.normalize_code("Dolares") == "USD"
    assert cur.normalize_code("pesos argentinos") == "ARS"
    assert cur.normalize_code("ARS") == "ARS"
    # `$` solo es ambiguo: cae en el valor por defecto.
    assert cur.normalize_code("$", "ARS") == "ARS"
    assert cur.normalize_code("", "ARS") == "ARS"


def test_ingesta_con_columna_de_moneda(tmp_path):
    path = tmp_path / "mixto.csv"
    path.write_text(
        "fecha,concepto,categoria,persona,importe,moneda\n"
        "2026-03-01,Supermercado,Comida,Ana,45000,ARS\n"
        "2026-03-02,Hosting,Servicios,Miguel,12,USD\n"
        "2026-03-03,Libro,Ocio,Ana,8500,pesos\n",
        encoding="utf-8",
    )
    parsed = parse_file(path, default_currency="ARS")
    assert [r["currency"] for r in parsed.rows] == ["ARS", "USD", "ARS"]


def test_ingesta_detecta_dolar_en_el_importe(tmp_path):
    path = tmp_path / "tarjeta.csv"
    path.write_text(
        "fecha;concepto;persona;importe\n"
        "2026-03-01;Verduleria;Ana;$ 12.400,00\n"
        "2026-03-05;Suscripcion;Miguel;US$ 9,99\n",
        encoding="utf-8",
    )
    parsed = parse_file(path, default_currency="ARS")
    assert parsed.rows[0]["currency"] == "ARS"
    assert parsed.rows[0]["amount"] == 12400.00
    assert parsed.rows[1]["currency"] == "USD"
    assert parsed.rows[1]["amount"] == 9.99


def test_moneda_por_defecto_del_archivo(tmp_path):
    path = tmp_path / "usd.csv"
    path.write_text(
        "fecha,concepto,persona,importe\n2026-03-01,Servidor,Miguel,25\n",
        encoding="utf-8",
    )
    parsed = parse_file(path, default_currency="USD")
    assert parsed.rows[0]["currency"] == "USD"
    assert parsed.mapping["default_currency"] == "USD"

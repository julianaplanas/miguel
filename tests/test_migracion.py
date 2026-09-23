"""La columna `source` se agrega a bases creadas por la version anterior."""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, text

from app.db import _ensure_columns


def _base_anterior(path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE exchange_rates ("
        " code VARCHAR(8) PRIMARY KEY, base VARCHAR(8), rate FLOAT, updated_at DATETIME)"
    )
    con.execute("INSERT INTO exchange_rates VALUES ('USD','ARS',1450.0,'2026-09-22 10:00:00')")
    con.commit()
    con.close()


def test_agrega_la_columna_y_conserva_los_datos(tmp_path):
    path = tmp_path / "app.db"
    _base_anterior(path)
    engine = create_engine(f"sqlite:///{path}")

    creadas = _ensure_columns(engine)
    assert creadas == ["exchange_rates.source"]

    with engine.connect() as conn:
        fila = conn.execute(text("SELECT code, rate, source FROM exchange_rates")).one()
    assert fila.code == "USD"
    assert fila.rate == 1450.0
    # Lo que ya estaba se cargo a mano: queda marcado asi para que
    # "Actualizar cotizaciones" no lo pise.
    assert fila.source == "manual"


def test_es_idempotente(tmp_path):
    path = tmp_path / "app.db"
    _base_anterior(path)
    engine = create_engine(f"sqlite:///{path}")
    assert _ensure_columns(engine) == ["exchange_rates.source"]
    assert _ensure_columns(engine) == []

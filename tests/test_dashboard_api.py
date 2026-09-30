"""Lo que el dashboard le pide al servidor: categorias, meses y orden."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

from tests.conftest import reset_db

# Diez categorias distintas en tres meses, para que haya mas de las que
# entran en un grafico apilado.
CSV_FILAS = ["fecha,concepto,categoria,persona,importe"]
CATEGORIAS = [
    "Supermercado", "Salud", "Transporte", "Ocio", "Ropa",
    "Servicios", "Impuestos", "Mascotas", "Educacion", "Restaurantes",
]
for _mes in (6, 7, 8):
    for _i, _cat in enumerate(CATEGORIAS):
        CSV_FILAS.append(f"2026-{_mes:02d}-{_i + 1:02d},{_cat.upper()} SRL,{_cat},Ana,{(_i + 1) * 1000}")
CSV = "\n".join(CSV_FILAS) + "\n"


@pytest.fixture
def auth():
    reset_db()
    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "tester", "password": "secreto123", "next": "/"},
            follow_redirects=False,
        )
        client.post(
            "/archivos/upload",
            files={"files": ("gastos.csv", io.BytesIO(CSV.encode("utf-8")), "text/csv")},
            data={"default_person": "", "default_currency": "ARS"},
            follow_redirects=False,
        )
        yield client


def test_el_ranking_lleva_todas_las_categorias(auth):
    """Nada de esconder las propias en un 'Otros': cada barra va etiquetada."""
    data = auth.get("/api/resumen").json()
    etiquetas = [c["label"] for c in data["by_category"]]
    assert set(etiquetas) == set(CATEGORIAS)
    assert "Otros" not in etiquetas


def test_hay_gasto_por_categoria_y_mes(auth):
    data = auth.get("/api/resumen").json()
    por_mes = data["month_category"]
    assert [f["month"] for f in por_mes["rows"]] == ["2026-06", "2026-07", "2026-08"]
    # El apilado si tiene tope: mas de ocho series no se distinguen.
    assert len(por_mes["categories"]) <= 8
    assert "Otros" in por_mes["categories"]

    # Cada mes suma lo mismo que el total de ese mes en la evolucion.
    for fila, mes in zip(por_mes["rows"], data["by_month"]):
        assert round(sum(fila["values"]) + fila["others"], 2) == mes["expense"]


def test_ya_no_se_calcula_el_gasto_por_dia_de_la_semana(auth):
    assert "by_weekday" not in auth.get("/api/resumen").json()


def test_los_meses_disponibles_estan_en_las_opciones(auth):
    opciones = auth.get("/api/opciones").json()
    assert opciones["months"] == ["2026-08", "2026-07", "2026-06"]


@pytest.mark.parametrize(
    "campo,numerico",
    [
        ("descripcion", False),
        ("categoria", False),
        ("persona", False),
        ("moneda", False),
        ("fecha", False),
        ("importe", True),
    ],
)
def test_se_puede_ordenar_por_cualquier_columna(auth, campo, numerico):
    clave = (lambda v: v) if numerico else (lambda v: str(v).lower())
    asc = auth.get(f"/api/transacciones?orden={campo}&dir=asc").json()
    desc = auth.get(f"/api/transacciones?orden={campo}&dir=desc").json()

    valores_asc = [f[campo] for f in asc["rows"]]
    assert valores_asc == sorted(valores_asc, key=clave)
    valores_desc = [f[campo] for f in desc["rows"]]
    assert valores_desc == sorted(valores_desc, key=clave, reverse=True)
    assert asc["orden"] == campo and asc["dir"] == "asc"


def test_el_orden_se_aplica_antes_de_paginar(auth):
    """Ordenar en el navegador solo ordenaria la pagina cargada."""
    pagina = auth.get("/api/transacciones?orden=importe&dir=desc&limit=5").json()
    assert pagina["total"] == 30
    assert len(pagina["rows"]) == 5
    # El mas caro de todo el conjunto, no el mas caro de los cinco primeros.
    assert pagina["rows"][0]["importe"] == 10000.0


def test_un_orden_que_no_existe_cae_en_fecha(auth):
    data = auth.get("/api/transacciones?orden=cualquiera").json()
    assert data["orden"] == "fecha"

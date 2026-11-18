"""El chat consulta los datos con herramientas y verifica cada cifra.

Lo que motiva esto: preguntado "por banco", el modelo invento Galicia,
Santander e ICBC con importes a dos decimales. Ahora cada cifra tiene que
salir de una consulta, y lo que no sale se le devuelve para corregir o se
marca en la respuesta.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.chat_tools import agrupar, buscar_movimientos, listar_valores
from app.db import SessionLocal
from app.main import app
from app.verificar import cifras_en, numeros_en, verificar

from tests.conftest import reset_db

GALICIA = (
    "fecha,concepto,persona,importe,moneda\n"
    "2026-05-02,COTO DIGITAL,Ana,48200.50,ARS\n"
    "2026-05-05,NETFLIX,Ana,9.99,USD\n"
    "2026-05-09,SUELDO,Ana,-900000,ARS\n"
    "2026-06-01,COTO DIGITAL,Miguel,51799.50,ARS\n"
)
SANTANDER = (
    "fecha,concepto,persona,importe\n"
    "2026-05-03,YPF,Miguel,35000\n"
    "2026-06-04,FARMACITY,Miguel,18300\n"
)


# ---------- verificador ----------


def test_cifras_en_entiende_formatos_y_salta_fechas():
    texto = (
        "El 05/03/2026 gastaste 1.234,56 (12,5%) y en 2026-05 fueron 48.200,50. "
        "Tienes 3 archivos y 130 movimientos. Lista:\n1. uno\n2. dos\nEn ingles 1,234.56."
    )
    vistas = [(c["texto"], c["valor"]) for c in cifras_en(texto)]
    assert ("1.234,56", 1234.56) in vistas
    assert ("12,5%", 12.5) in vistas
    assert ("48.200,50", 48200.5) in vistas
    assert ("130", 130.0) in vistas
    assert ("1,234.56", 1234.56) in vistas
    textos = [t for t, _ in vistas]
    assert "2026" not in textos and "05" not in textos and "3" not in textos


def test_verificar_acepta_lo_que_sale_de_los_datos():
    # Lo que devuelve `agrupar`: los grupos y el total por moneda.
    evidencia = {100000.0, 53300.0, 35000.0, 18300.0, 9.99, 900000.0, 153300.0}
    ok = (
        "Supermercado: 100.000,00. YPF 35.000. Santander: 53.300 (35.000 + 18.300). "
        "Supermercado es el 65,2% del gasto en ARS de 153.300,00. "
        "Diferencia: 46.700,00. En millones: 0,9 millones de sueldo. "
        "Supermercado gasta un 87,6% mas que Santander."
    )
    assert verificar(ok, evidencia) == []


def test_verificar_pilla_cifras_inventadas():
    evidencia = {48200.5, 51799.5, 35000.0, 18300.0}
    malo = "| Galicia | 8.284.184,76 | 85,7% |\n| Santander | 1.388.119,20 | 14,3% |"
    assert verificar(malo, evidencia) == ["8.284.184,76", "85,7%", "1.388.119,20", "14,3%"]


def test_verificar_mira_tambien_los_graficos():
    evidencia = {100.0, 50.0}
    texto = 'Grafico:\n```chart\n{"labels": ["a", "b"], "series": [{"data": [100, 77]}]}\n```'
    assert verificar(texto, evidencia) == ["77"]


def test_numeros_en_recorre_el_json_y_no_lee_descripciones():
    datos = {"grupos": [{"gasto": 10.5, "descripcion": "PAGO 999", "activo": True}], "n": 3}
    assert numeros_en(datos) == {10.5, 3.0}


# ---------- herramientas ----------


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


def _subir(client, nombre, contenido):
    client.post(
        "/archivos/upload",
        files={"files": (nombre, io.BytesIO(contenido.encode()), "text/csv")},
        data={"revisar": ""},
        follow_redirects=False,
    )


def test_agrupar_por_archivo_no_mezcla_monedas(auth):
    _subir(auth, "galicia.csv", GALICIA)
    _subir(auth, "santander.csv", SANTANDER)
    with SessionLocal() as db:
        r = agrupar(db, {"por": "archivo"})
    grupos = {(g["archivo"], g["moneda"]): g for g in r["grupos"]}
    assert grupos[("galicia.csv", "ARS")]["gasto"] == pytest.approx(100000.0)
    assert grupos[("galicia.csv", "ARS")]["ingreso"] == pytest.approx(900000.0)
    assert grupos[("galicia.csv", "USD")]["gasto"] == pytest.approx(9.99)
    assert grupos[("santander.csv", "ARS")]["movimientos"] == 2
    assert r["total_por_moneda"]["ARS"]["gasto"] == pytest.approx(153300.0)


def test_agrupar_con_filtros_y_buscar(auth):
    _subir(auth, "galicia.csv", GALICIA)
    _subir(auth, "santander.csv", SANTANDER)
    with SessionLocal() as db:
        mayo = agrupar(db, {"por": "persona", "desde": "2026-05-01", "hasta": "2026-05-31", "moneda": "ARS"})
        coto = buscar_movimientos(db, {"texto": "coto", "orden": "importe"})
        valores = listar_valores(db, {"dimension": "mes"})
        mal = agrupar(db, {"por": "sucursal"})
    assert {g["persona"]: g["gasto"] for g in mayo["grupos"]} == {"Ana": 48200.5, "Miguel": 35000.0}
    assert coto["encontrados"] == 2 and coto["movimientos"][0]["importe"] == 51799.5
    assert [v["valor"] for v in valores["valores"]] == ["2026-05", "2026-06"]
    assert "error" in mal


# ---------- el bucle completo con un modelo falso ----------


class ModeloFalso:
    """Simula OpenRouter: primero pide una consulta, luego contesta."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.payloads = []

    async def __call__(self, payload):
        self.payloads.append(payload)
        turno = self.respuestas.pop(0)
        if turno.get("tool"):
            mensaje = {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": turno["tool"], "arguments": json.dumps(turno["args"])},
                    }
                ],
            }
        else:
            mensaje = {"content": turno["content"]}
        return {"choices": [{"message": mensaje}]}


@pytest.fixture
def chat(auth, monkeypatch):
    monkeypatch.setattr("app.config.Settings.chat_enabled", property(lambda self: True))
    monkeypatch.setattr("app.config.Settings.openrouter_api_key", "test", raising=False)
    _subir(auth, "galicia.csv", GALICIA)
    _subir(auth, "santander.csv", SANTANDER)
    return auth


def test_consulta_y_responde_con_cifras_de_verdad(chat, monkeypatch):
    modelo = ModeloFalso(
        [
            {"tool": "agrupar", "args": {"por": "archivo", "moneda": "ARS"}},
            {"content": "| Archivo | Gasto ARS |\n|---|---|\n| galicia.csv | 100.000,00 |\n| santander.csv | 53.300,00 |"},
        ]
    )
    monkeypatch.setattr(llm, "_request", modelo)
    r = chat.post("/api/chat", json={"message": "cuanto gaste por archivo?"}).json()
    assert r["sin_respaldo"] == []
    assert "⚠" not in r["reply"]
    assert r["consultas"] == [{"herramienta": "agrupar", "argumentos": {"por": "archivo", "moneda": "ARS"}}]
    # La primera llamada llevo las herramientas y la segunda, el resultado.
    assert modelo.payloads[0]["tools"]
    assert modelo.payloads[1]["messages"][-1]["role"] == "tool"
    assert "100000.0" in modelo.payloads[1]["messages"][-1]["content"]


def test_una_cifra_inventada_se_devuelve_para_corregir(chat, monkeypatch):
    modelo = ModeloFalso(
        [
            {"tool": "agrupar", "args": {"por": "archivo"}},
            {"content": "Galicia 8.284.184,76 y Santander 53.300,00."},
            {"content": "Galicia 100.000,00 y Santander 53.300,00."},
        ]
    )
    monkeypatch.setattr(llm, "_request", modelo)
    r = chat.post("/api/chat", json={"message": "por banco?"}).json()
    assert r["sin_respaldo"] == []
    assert r["reply"] == "Galicia 100.000,00 y Santander 53.300,00."
    correccion = modelo.payloads[2]["messages"][-1]
    assert correccion["role"] == "user" and "8.284.184,76" in correccion["content"]


def test_si_insiste_la_cifra_queda_marcada(chat, monkeypatch):
    modelo = ModeloFalso(
        [
            {"content": "ICBC: 1.388.119,20."},
            {"content": "ICBC: 1.388.119,20, seguro."},
        ]
    )
    monkeypatch.setattr(llm, "_request", modelo)
    r = chat.post("/api/chat", json={"message": "y el ICBC?"}).json()
    assert r["sin_respaldo"] == ["1.388.119,20"]
    assert "⚠ Cifras sin respaldo" in r["reply"] and "1.388.119,20" in r["reply"]
    assert len(modelo.payloads) == 2  # una sola ronda de correccion


def test_modelo_sin_herramientas_sigue_funcionando(chat, monkeypatch):
    llamadas = []

    async def sin_tools(payload):
        llamadas.append(payload)
        if "tools" in payload:
            raise llm.OpenRouterError("OpenRouter respondio 404: No endpoints found that support tool use")
        return {"choices": [{"message": {"content": "Hay 6 movimientos."}}]}

    monkeypatch.setattr(llm, "_request", sin_tools)
    r = chat.post("/api/chat", json={"message": "cuantos movimientos hay?"}).json()
    assert r["reply"] == "Hay 6 movimientos."
    assert "tools" not in llamadas[-1]

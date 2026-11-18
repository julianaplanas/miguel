"""Herramientas con las que el modelo del chat consulta los datos.

La idea: el modelo no recibe una foto fija de los datos y "recuerda"
cifras, sino que pide lo que necesita y cada numero que da sale de una
consulta hecha aqui, con pandas-free SQL sencillo sobre los archivos
activos. Lo que devuelve cada herramienta es ademas lo que la etapa de
verificacion (app/verificar.py) usa como unica fuente de verdad.

Los totales se devuelven moneda por moneda, sin convertir: mezclar pesos
y dolares es justo el error que no queremos, y el modelo ya tiene la
regla de no sumarlos.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.analytics import Filters, fetch_rows, parse_date

DIMENSIONES = {
    "categoria": "category",
    "persona": "person",
    "mes": "mes",
    "archivo": "filename",
    "cuenta": "account",
    "moneda": "currency",
    "descripcion": "description",
}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "agrupar",
            "description": (
                "Total de gasto, de ingreso y numero de movimientos agrupados por una "
                "dimension, moneda por moneda. Es la forma de responder 'cuanto gaste "
                "en X', 'por mes', 'por archivo/banco/tarjeta' (el archivo es el resumen "
                "subido; su nombre es la unica pista del banco), 'por comercio' "
                "(descripcion). Admite filtros."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "por": {"type": "string", "enum": list(DIMENSIONES)},
                    "desde": {"type": "string", "description": "AAAA-MM-DD inclusive"},
                    "hasta": {"type": "string", "description": "AAAA-MM-DD inclusive"},
                    "categorias": {"type": "array", "items": {"type": "string"}},
                    "personas": {"type": "array", "items": {"type": "string"}},
                    "moneda": {"type": "string", "description": "Solo esa moneda (ARS, USD...)"},
                    "limite": {"type": "integer", "description": "Maximo de grupos (por defecto 30)"},
                },
                "required": ["por"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_movimientos",
            "description": (
                "Lista movimientos concretos con fecha, descripcion, categoria, persona, "
                "importe, moneda y archivo. Sirve para ver que hay detras de un total, "
                "buscar un comercio, encontrar los gastos mas grandes o repetidos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string", "description": "Trozo de la descripcion, sin distinguir mayusculas"},
                    "desde": {"type": "string"},
                    "hasta": {"type": "string"},
                    "categorias": {"type": "array", "items": {"type": "string"}},
                    "personas": {"type": "array", "items": {"type": "string"}},
                    "moneda": {"type": "string"},
                    "importe_minimo": {"type": "number"},
                    "orden": {"type": "string", "enum": ["fecha", "importe"]},
                    "limite": {"type": "integer", "description": "Maximo de filas (por defecto 40, tope 200)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_valores",
            "description": (
                "Valores distintos de una dimension (categorias, personas, meses, "
                "archivos, cuentas, monedas) con cuantos movimientos tiene cada uno. "
                "Usalo antes de filtrar para escribir los nombres exactamente."
            ),
            "parameters": {
                "type": "object",
                "properties": {"dimension": {"type": "string", "enum": list(DIMENSIONES)}},
                "required": ["dimension"],
            },
        },
    },
]


def _filtros(args: dict[str, Any]) -> Filters:
    return Filters(
        date_from=parse_date(args.get("desde")),
        date_to=parse_date(args.get("hasta")),
        persons=[str(p) for p in args.get("personas") or []] or None,
        categories=[str(c) for c in args.get("categorias") or []] or None,
        currency=(str(args.get("moneda") or "").upper() or None),
    )


def _valor(row: dict[str, Any], dimension: str) -> str:
    campo = DIMENSIONES[dimension]
    if campo == "mes":
        return row["date"].strftime("%Y-%m") if row["date"] else "(sin fecha)"
    return str(row.get(campo) or "(sin dato)")


def agrupar(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    dimension = str(args.get("por") or "")
    if dimension not in DIMENSIONES:
        return {"error": f"'por' tiene que ser una de: {', '.join(DIMENSIONES)}"}
    filas = fetch_rows(db, _filtros(args))
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for r in filas:
        clave = (_valor(r, dimension), r["currency"])
        g = grupos.setdefault(
            clave,
            {dimension: clave[0], "moneda": clave[1], "gasto": 0.0, "ingreso": 0.0, "movimientos": 0},
        )
        g["movimientos"] += 1
        if r["amount"] > 0:
            g["gasto"] += r["amount"]
        else:
            g["ingreso"] += -r["amount"]
    salida = sorted(grupos.values(), key=lambda g: -g["gasto"])
    for g in salida:
        g["gasto"] = round(g["gasto"], 2)
        g["ingreso"] = round(g["ingreso"], 2)
    limite = max(1, min(int(args.get("limite") or 30), 200))
    recortado = len(salida) > limite
    totales: dict[str, dict[str, float]] = defaultdict(lambda: {"gasto": 0.0, "ingreso": 0.0, "movimientos": 0})
    for g in salida:
        t = totales[g["moneda"]]
        t["gasto"] = round(t["gasto"] + g["gasto"], 2)
        t["ingreso"] = round(t["ingreso"] + g["ingreso"], 2)
        t["movimientos"] += g["movimientos"]
    return {
        "por": dimension,
        "grupos": salida[:limite],
        "grupos_totales": len(salida),
        "recortado": recortado,
        "total_por_moneda": dict(totales),
        "movimientos_considerados": len(filas),
    }


def buscar_movimientos(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    filas = fetch_rows(db, _filtros(args))
    texto = str(args.get("texto") or "").strip().lower()
    if texto:
        filas = [r for r in filas if texto in r["description"].lower()]
    minimo = args.get("importe_minimo")
    if minimo is not None:
        filas = [r for r in filas if abs(r["amount"]) >= float(minimo)]
    if (args.get("orden") or "fecha") == "importe":
        filas.sort(key=lambda r: -abs(r["amount"]))
    else:
        filas.sort(key=lambda r: r["date"] or dt.date.min, reverse=True)
    limite = max(1, min(int(args.get("limite") or 40), 200))
    return {
        "encontrados": len(filas),
        "mostrados": min(len(filas), limite),
        "movimientos": [
            {
                "fecha": r["date"].isoformat() if r["date"] else None,
                "descripcion": r["description"][:100],
                "categoria": r["category"],
                "persona": r["person"],
                "importe": round(r["amount"], 2),
                "moneda": r["currency"],
                "archivo": r["filename"],
            }
            for r in filas[:limite]
        ],
    }


def listar_valores(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    dimension = str(args.get("dimension") or "")
    if dimension not in DIMENSIONES:
        return {"error": f"'dimension' tiene que ser una de: {', '.join(DIMENSIONES)}"}
    conteo: dict[str, int] = defaultdict(int)
    for r in fetch_rows(db, Filters()):
        conteo[_valor(r, dimension)] += 1
    valores = sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "dimension": dimension,
        "valores": [{"valor": v, "movimientos": n} for v, n in valores[:200]],
        "distintos": len(valores),
    }


HANDLERS = {
    "agrupar": agrupar,
    "buscar_movimientos": buscar_movimientos,
    "listar_valores": listar_valores,
}


def run_tool(db: Session, nombre: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(nombre)
    if handler is None:
        return {"error": f"No existe la herramienta '{nombre}'."}
    try:
        return handler(db, args or {})
    except Exception as exc:  # noqa: BLE001 - el modelo recibe el motivo y reintenta
        return {"error": f"La consulta fallo: {exc}"}

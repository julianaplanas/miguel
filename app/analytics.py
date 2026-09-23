"""Agregaciones sobre las transacciones de los archivos activos.

Regla central: nunca se suman importes de monedas distintas. O se mira una
moneda concreta, o se convierten todas a la moneda base con los tipos de
cambio que el usuario haya cargado. Si falta algun tipo de cambio, el
resumen lo dice en vez de devolver un total inventado.
"""
from __future__ import annotations

import bisect
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExchangeRate, RateHistory, Transaction, UploadedFile
from app.preferences import MODE_HISTORICAL, base_currency, conversion_mode


@dataclass
class Filters:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    persons: list[str] | None = None
    categories: list[str] | None = None
    file_ids: list[int] | None = None
    # None o "" = todas las monedas, convertidas a la base.
    currency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "persons": self.persons or [],
            "categories": self.categories or [],
            "file_ids": self.file_ids or [],
            "currency": self.currency or "",
        }


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def get_rates(db: Session) -> dict[str, float]:
    """Tipos de cambio hacia la base, incluida la base (=1)."""
    base = base_currency(db)
    rates: dict[str, float] = {base: 1.0}
    for row in db.execute(select(ExchangeRate)).scalars().all():
        code = (row.code or "").upper()
        if not code or code == base:
            continue
        # Un tipo guardado contra otra base ya no sirve.
        if row.base and row.base.upper() != base:
            continue
        if row.rate and row.rate > 0:
            rates[code] = float(row.rate)
    return rates



def get_history(db: Session, base: str) -> dict[str, tuple[list[dt.date], list[float]]]:
    """Serie historica por moneda: (fechas ordenadas, tipos) hacia `base`."""
    filas = db.execute(
        select(RateHistory.code, RateHistory.date, RateHistory.rate)
        .where(RateHistory.base == base)
        .order_by(RateHistory.code, RateHistory.date)
    ).all()
    series: dict[str, tuple[list[dt.date], list[float]]] = {}
    for code, fecha, valor in filas:
        code = (code or "").upper()
        if not fecha or not valor or valor <= 0:
            continue
        fechas, valores = series.setdefault(code, ([], []))
        fechas.append(fecha)
        valores.append(float(valor))
    return series


class Converter:
    """Convierte importes a la moneda base.

    En modo `historical` cada movimiento usa el tipo de cambio de su propia
    fecha (el del dia habil anterior si ese dia no cotizo). Lo que no tenga
    historico cae al tipo actual, y esas filas se cuentan aparte para poder
    avisarlo en vez de disimularlo.
    """

    def __init__(self, db: Session, base: str, mode: str) -> None:
        self.base = base
        self.mode = mode
        self.current = get_rates(db)
        self.history = get_history(db, base) if mode == MODE_HISTORICAL else {}
        self.fallbacks = 0
        self.fallback_currencies: set[str] = set()

    def has_any(self, code: str) -> bool:
        return code == self.base or code in self.current or code in self.history

    def _historical(self, code: str, date: dt.date) -> float | None:
        serie = self.history.get(code)
        if not serie:
            return None
        fechas, valores = serie
        # Ultima cotizacion en la fecha o antes (fines de semana, feriados).
        indice = bisect.bisect_right(fechas, date) - 1
        if indice < 0:
            return None
        return valores[indice]

    def factor(self, code: str, date: dt.date | None) -> float | None:
        if code == self.base:
            return 1.0
        if self.mode == MODE_HISTORICAL and date is not None:
            valor = self._historical(code, date)
            if valor is not None:
                return valor
        actual = self.current.get(code)
        if actual is None:
            return None
        if self.mode == MODE_HISTORICAL:
            self.fallbacks += 1
            self.fallback_currencies.add(code)
        return actual


def fetch_rows(db: Session, filters: Filters) -> list[dict[str, Any]]:
    """Devuelve las transacciones de los archivos ACTIVOS que pasan los filtros."""
    base = base_currency(db)
    stmt = (
        select(
            Transaction.id,
            Transaction.date,
            Transaction.amount,
            Transaction.currency,
            Transaction.category,
            Transaction.person,
            Transaction.description,
            Transaction.account,
            Transaction.file_id,
            UploadedFile.filename,
        )
        .join(UploadedFile, Transaction.file_id == UploadedFile.id)
        .where(UploadedFile.is_active.is_(True))
    )
    if filters.file_ids:
        stmt = stmt.where(Transaction.file_id.in_(filters.file_ids))
    if filters.date_from:
        stmt = stmt.where(Transaction.date >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Transaction.date <= filters.date_to)
    if filters.persons:
        stmt = stmt.where(Transaction.person.in_(filters.persons))
    if filters.categories:
        stmt = stmt.where(Transaction.category.in_(filters.categories))

    rows = []
    for r in db.execute(stmt).all():
        code = (r.currency or base).upper()
        if filters.currency and code != filters.currency.upper():
            continue
        rows.append(
            {
                "id": r.id,
                "date": r.date,
                "amount": float(r.amount or 0.0),
                "currency": code,
                "category": r.category or "Sin categoria",
                "person": r.person or "Sin asignar",
                "description": r.description or "",
                "account": r.account or "",
                "file_id": r.file_id,
                "filename": r.filename,
            }
        )
    return rows


def currencies_in(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({r["currency"] for r in rows})


def _sorted_totals(totals: dict[str, float], limit: int | None = None) -> list[dict[str, Any]]:
    items = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    if limit and len(items) > limit:
        head = items[:limit]
        rest = sum(v for _, v in items[limit:])
        if rest:
            head.append(("Otros", rest))
        items = head
    return [{"label": k, "value": round(v, 2)} for k, v in items]


def build_summary(db: Session, filters: Filters, top_n: int = 8) -> dict[str, Any]:
    base = base_currency(db)
    modo = conversion_mode(db)
    rows = fetch_rows(db, filters)
    conv = Converter(db, base, modo)
    rates = conv.current

    presentes = currencies_in(rows)
    seleccionada = (filters.currency or "").upper()
    convertido = not seleccionada and len(presentes) > 1
    # Monedas presentes sin ningun tipo de cambio (ni actual ni historico).
    faltan = [c for c in presentes if not conv.has_any(c)] if convertido else []

    # El desglose por moneda se calcula sobre TODO lo que hay en el periodo,
    # incluso lo que despues no se pueda convertir: es la vista que explica
    # por que el total no cuadra con lo que el usuario esperaba.
    todas = rows

    if convertido and faltan:
        # Sin todos los tipos no hay total honesto: nos quedamos con la moneda
        # mas frecuente y avisamos para que el usuario cargue los que faltan.
        conteo: dict[str, int] = defaultdict(int)
        for r in rows:
            conteo[r["currency"]] += 1
        seleccionada = max(conteo, key=lambda c: conteo[c])
        rows = [r for r in rows if r["currency"] == seleccionada]
        convertido = False

    if seleccionada or len(presentes) == 1:
        # Una sola moneda: se muestra tal cual, sin convertir ni pedir tipo
        # de cambio. Convertirla y seguir etiquetandola con su codigo
        # original daria cifras que no son ni una cosa ni la otra.
        moneda_salida = seleccionada or (presentes[0] if presentes else base)
        for r in rows:
            r["valor"] = r["amount"]
    else:
        moneda_salida = base
        for r in rows:
            r["valor"] = r["amount"] * (conv.factor(r["currency"], r["date"]) or 1.0)

    gastos = [r for r in rows if r["valor"] > 0]
    ingresos = [r for r in rows if r["valor"] < 0]

    total_gasto = sum(r["valor"] for r in gastos)
    total_ingreso = -sum(r["valor"] for r in ingresos)

    por_categoria: dict[str, float] = defaultdict(float)
    por_persona: dict[str, float] = defaultdict(float)
    por_mes: dict[str, float] = defaultdict(float)
    por_mes_ingreso: dict[str, float] = defaultdict(float)
    por_persona_categoria: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    por_dia: dict[int, float] = defaultdict(float)
    por_moneda: dict[str, dict[str, float]] = defaultdict(lambda: {"gasto": 0.0, "ingreso": 0.0, "movimientos": 0})

    for r in todas:
        entrada = por_moneda[r["currency"]]
        entrada["movimientos"] += 1
        if r["amount"] > 0:
            entrada["gasto"] += r["amount"]
        else:
            entrada["ingreso"] += -r["amount"]

    for r in gastos:
        por_categoria[r["category"]] += r["valor"]
        por_persona[r["person"]] += r["valor"]
        por_persona_categoria[r["person"]][r["category"]] += r["valor"]
        if r["date"]:
            por_mes[r["date"].strftime("%Y-%m")] += r["valor"]
            por_dia[r["date"].weekday()] += r["valor"]
    for r in ingresos:
        if r["date"]:
            por_mes_ingreso[r["date"].strftime("%Y-%m")] += -r["valor"]

    meses = sorted(set(por_mes) | set(por_mes_ingreso))
    fechas = [r["date"] for r in rows if r["date"]]

    top_categorias = _sorted_totals(por_categoria, top_n)
    etiquetas_top = [c["label"] for c in top_categorias if c["label"] != "Otros"]
    personas = [p["label"] for p in _sorted_totals(por_persona)]

    cruce = []
    for persona in personas:
        totales = por_persona_categoria[persona]
        fila = {"person": persona, "values": []}
        for etiqueta in etiquetas_top:
            fila["values"].append(round(totales.get(etiqueta, 0.0), 2))
        otros = sum(v for k, v in totales.items() if k not in etiquetas_top)
        fila["others"] = round(otros, 2)
        cruce.append(fila)

    dias = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
    top_transacciones = sorted(gastos, key=lambda r: r["valor"], reverse=True)[:15]

    return {
        "currency": moneda_salida,
        "converted": convertido,
        "mode": modo,
        "historical_fallbacks": conv.fallbacks if convertido else 0,
        "fallback_currencies": sorted(conv.fallback_currencies) if convertido else [],
        "currencies": presentes,
        "missing_rates": faltan,
        "rates": {c: rates[c] for c in presentes if c in rates},
        "by_currency": [
            {
                "code": code,
                "expense": round(v["gasto"], 2),
                "income": round(v["ingreso"], 2),
                "transactions": int(v["movimientos"]),
                "rate": rates.get(code),
            }
            for code, v in sorted(por_moneda.items())
        ],
        "kpis": {
            "total_expense": round(total_gasto, 2),
            "total_income": round(total_ingreso, 2),
            "net": round(total_ingreso - total_gasto, 2),
            "transactions": len(rows),
            "expense_count": len(gastos),
            "average_expense": round(total_gasto / len(gastos), 2) if gastos else 0.0,
            "monthly_average": round(total_gasto / len(por_mes), 2) if por_mes else 0.0,
            "people": len(por_persona),
            "categories": len(por_categoria),
            "date_min": min(fechas).isoformat() if fechas else None,
            "date_max": max(fechas).isoformat() if fechas else None,
        },
        "by_category": top_categorias,
        "by_person": _sorted_totals(por_persona),
        "by_month": [
            {
                "label": m,
                "expense": round(por_mes.get(m, 0.0), 2),
                "income": round(por_mes_ingreso.get(m, 0.0), 2),
            }
            for m in meses
        ],
        "by_weekday": [
            {"label": dias[i], "value": round(por_dia.get(i, 0.0), 2)} for i in range(7)
        ],
        "person_category": {
            "categories": etiquetas_top + (["Otros"] if any(f["others"] for f in cruce) else []),
            "rows": cruce,
        },
        "top_transactions": [
            {
                "date": r["date"].isoformat() if r["date"] else "",
                "description": r["description"][:120],
                "category": r["category"],
                "person": r["person"],
                "amount": round(r["valor"], 2),
                "original_amount": round(r["amount"], 2),
                "original_currency": r["currency"],
            }
            for r in top_transacciones
        ],
        "filters": filters.as_dict(),
    }


def available_options(db: Session) -> dict[str, Any]:
    """Personas, categorias, monedas y rango de fechas de los archivos activos."""
    base = base_currency(db)
    stmt = (
        select(Transaction.person, Transaction.category, Transaction.date, Transaction.currency)
        .join(UploadedFile, Transaction.file_id == UploadedFile.id)
        .where(UploadedFile.is_active.is_(True))
    )
    personas: set[str] = set()
    categorias: set[str] = set()
    monedas: set[str] = set()
    fechas: list[dt.date] = []
    for persona, categoria, fecha, moneda in db.execute(stmt).all():
        if persona:
            personas.add(persona)
        if categoria:
            categorias.add(categoria)
        monedas.add((moneda or base).upper())
        if fecha:
            fechas.append(fecha)
    rates = get_rates(db)
    historia = get_history(db, base)
    return {
        "persons": sorted(personas),
        "categories": sorted(categorias),
        "currencies": sorted(monedas),
        "base_currency": base,
        "conversion_mode": conversion_mode(db),
        "missing_rates": sorted(
            c for c in monedas if c != base and c not in rates and c not in historia
        ),
        "date_min": min(fechas).isoformat() if fechas else None,
        "date_max": max(fechas).isoformat() if fechas else None,
    }

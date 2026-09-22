"""Agregaciones sobre las transacciones de los archivos activos."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction, UploadedFile


@dataclass
class Filters:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    persons: list[str] | None = None
    categories: list[str] | None = None
    file_ids: list[int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "persons": self.persons or [],
            "categories": self.categories or [],
            "file_ids": self.file_ids or [],
        }


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def fetch_rows(db: Session, filters: Filters) -> list[dict[str, Any]]:
    """Devuelve las transacciones de los archivos ACTIVOS que pasan los filtros."""
    stmt = (
        select(
            Transaction.id,
            Transaction.date,
            Transaction.amount,
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
        rows.append(
            {
                "id": r.id,
                "date": r.date,
                "amount": float(r.amount or 0.0),
                "category": r.category or "Sin categoria",
                "person": r.person or "Sin asignar",
                "description": r.description or "",
                "account": r.account or "",
                "file_id": r.file_id,
                "filename": r.filename,
            }
        )
    return rows


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
    rows = fetch_rows(db, filters)
    expenses = [r for r in rows if r["amount"] > 0]
    incomes = [r for r in rows if r["amount"] < 0]

    total_expense = sum(r["amount"] for r in expenses)
    total_income = -sum(r["amount"] for r in incomes)

    by_category: dict[str, float] = defaultdict(float)
    by_person: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    by_month_income: dict[str, float] = defaultdict(float)
    by_person_category: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_weekday: dict[int, float] = defaultdict(float)

    for r in expenses:
        by_category[r["category"]] += r["amount"]
        by_person[r["person"]] += r["amount"]
        by_person_category[r["person"]][r["category"]] += r["amount"]
        if r["date"]:
            by_month[r["date"].strftime("%Y-%m")] += r["amount"]
            by_weekday[r["date"].weekday()] += r["amount"]
    for r in incomes:
        if r["date"]:
            by_month_income[r["date"].strftime("%Y-%m")] += -r["amount"]

    months = sorted(set(by_month) | set(by_month_income))
    dates = [r["date"] for r in rows if r["date"]]

    top_categories = _sorted_totals(by_category, top_n)
    top_category_labels = [c["label"] for c in top_categories if c["label"] != "Otros"]
    persons_sorted = [p["label"] for p in _sorted_totals(by_person)]

    stacked = []
    for person in persons_sorted:
        cat_totals = by_person_category[person]
        entry = {"person": person, "values": []}
        for label in top_category_labels:
            entry["values"].append(round(cat_totals.get(label, 0.0), 2))
        others = sum(v for k, v in cat_totals.items() if k not in top_category_labels)
        entry["others"] = round(others, 2)
        stacked.append(entry)

    weekday_names = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]

    top_transactions = sorted(expenses, key=lambda r: r["amount"], reverse=True)[:15]

    return {
        "kpis": {
            "total_expense": round(total_expense, 2),
            "total_income": round(total_income, 2),
            "net": round(total_income - total_expense, 2),
            "transactions": len(rows),
            "expense_count": len(expenses),
            "average_expense": round(total_expense / len(expenses), 2) if expenses else 0.0,
            "monthly_average": round(total_expense / len(by_month), 2) if by_month else 0.0,
            "people": len(by_person),
            "categories": len(by_category),
            "date_min": min(dates).isoformat() if dates else None,
            "date_max": max(dates).isoformat() if dates else None,
        },
        "by_category": top_categories,
        "by_person": _sorted_totals(by_person),
        "by_month": [
            {
                "label": m,
                "expense": round(by_month.get(m, 0.0), 2),
                "income": round(by_month_income.get(m, 0.0), 2),
            }
            for m in months
        ],
        "by_weekday": [
            {"label": weekday_names[i], "value": round(by_weekday.get(i, 0.0), 2)} for i in range(7)
        ],
        "person_category": {
            "categories": top_category_labels + (["Otros"] if any(s["others"] for s in stacked) else []),
            "rows": stacked,
        },
        "top_transactions": [
            {
                "date": r["date"].isoformat() if r["date"] else "",
                "description": r["description"][:120],
                "category": r["category"],
                "person": r["person"],
                "amount": round(r["amount"], 2),
            }
            for r in top_transactions
        ],
        "filters": filters.as_dict(),
    }


def available_options(db: Session) -> dict[str, Any]:
    """Personas, categorias y rango de fechas disponibles en los archivos activos."""
    stmt = (
        select(Transaction.person, Transaction.category, Transaction.date)
        .join(UploadedFile, Transaction.file_id == UploadedFile.id)
        .where(UploadedFile.is_active.is_(True))
    )
    persons: set[str] = set()
    categories: set[str] = set()
    dates: list[dt.date] = []
    for person, category, date in db.execute(stmt).all():
        if person:
            persons.add(person)
        if category:
            categories.add(category)
        if date:
            dates.append(date)
    return {
        "persons": sorted(persons),
        "categories": sorted(categories),
        "date_min": min(dates).isoformat() if dates else None,
        "date_max": max(dates).isoformat() if dates else None,
    }

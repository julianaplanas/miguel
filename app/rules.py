"""Aplicacion de las reglas de categorizacion sobre los movimientos."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categorize import (
    SOURCE_AI,
    SOURCE_FILE,
    SOURCE_MANUAL,
    SOURCE_NONE,
    SOURCE_RULE,
    UNCATEGORIZED,
    Rule,
    categorize,
    default_rules,
    is_uncategorized,
    normalize,
    sort_rules,
)
from app.models import CategoryRule, Transaction

# Origenes que el recategorizado puede sobrescribir. Lo que el usuario
# corrigio a mano y lo que vino con categoria en el archivo se respetan.
REPLACEABLE = {SOURCE_NONE, SOURCE_RULE, SOURCE_AI}


def load_rules(db: Session) -> list[Rule]:
    """Reglas del usuario + las de fabrica, ordenadas por especificidad."""
    propias = [
        Rule(pattern=r.pattern, category=r.category, source=r.source or "manual")
        for r in db.execute(select(CategoryRule)).scalars().all()
    ]
    return sort_rules(propias + default_rules())


def categorize_rows(rows: list[dict], rules: list[Rule]) -> int:
    """Rellena la categoria de las filas recien leidas de un archivo.

    Solo toca las que no traen categoria propia: si el archivo la tiene,
    manda el archivo.
    """
    aplicadas = 0
    for row in rows:
        if not is_uncategorized(row.get("category")):
            row["category_source"] = SOURCE_FILE
            continue
        resultado = categorize(row.get("description", ""), rules)
        if resultado is None:
            row["category"] = UNCATEGORIZED
            row["category_source"] = SOURCE_NONE
            continue
        row["category"], row["category_source"] = resultado
        aplicadas += 1
    return aplicadas


def recategorize(db: Session, only_uncategorized: bool = False) -> int:
    """Vuelve a aplicar las reglas a los movimientos ya guardados."""
    rules = load_rules(db)
    stmt = select(Transaction).where(
        Transaction.category_source.in_(REPLACEABLE) | Transaction.category_source.is_(None)
    )
    cambiados = 0
    for tx in db.execute(stmt).scalars().all():
        if only_uncategorized and not is_uncategorized(tx.category):
            continue
        resultado = categorize(tx.description or "", rules)
        if resultado is None:
            continue
        categoria, origen = resultado
        if tx.category == categoria and tx.category_source == origen:
            continue
        tx.category = categoria
        tx.category_source = origen
        cambiados += 1
    db.commit()
    return cambiados


def apply_rule(db: Session, pattern: str, category: str) -> int:
    """Aplica un patron concreto a todos los movimientos que lo contengan."""
    patron = normalize(pattern)
    if not patron:
        return 0
    cambiados = 0
    for tx in db.execute(select(Transaction)).scalars().all():
        if tx.category_source == SOURCE_MANUAL:
            continue
        if patron in normalize(tx.description or ""):
            tx.category = category
            tx.category_source = SOURCE_RULE
            cambiados += 1
    db.commit()
    return cambiados


def save_rule(db: Session, pattern: str, category: str, source: str = "manual") -> CategoryRule:
    """Guarda (o actualiza) una regla para ese patron."""
    patron = (pattern or "").strip()
    if not patron:
        raise ValueError("El patron no puede estar vacio")
    categoria = (category or "").strip() or UNCATEGORIZED

    existente = next(
        (
            r
            for r in db.execute(select(CategoryRule)).scalars().all()
            if normalize(r.pattern) == normalize(patron)
        ),
        None,
    )
    if existente:
        existente.category = categoria
        existente.source = source
        db.commit()
        return existente

    regla = CategoryRule(pattern=patron, category=categoria, source=source)
    db.add(regla)
    db.commit()
    return regla


def uncategorized_descriptions(db: Session, limit: int = 200) -> list[tuple[str, int, float]]:
    """Descripciones distintas sin categorizar, con cuantas veces aparecen.

    Sirve tanto para la pantalla de reglas como para pedirle sugerencias al
    modelo: agrupar por descripcion evita mandarle cientos de lineas iguales.
    """
    agrupadas: dict[str, dict] = {}
    for tx in db.execute(select(Transaction)).scalars().all():
        if not is_uncategorized(tx.category):
            continue
        clave = (tx.description or "").strip()
        if not clave:
            continue
        entrada = agrupadas.setdefault(clave, {"veces": 0, "total": 0.0})
        entrada["veces"] += 1
        entrada["total"] += abs(float(tx.amount or 0.0))

    ordenadas = sorted(agrupadas.items(), key=lambda kv: kv[1]["total"], reverse=True)
    return [(desc, datos["veces"], round(datos["total"], 2)) for desc, datos in ordenadas[:limit]]

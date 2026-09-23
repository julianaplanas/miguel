"""Pantalla de categorias: reglas, recategorizado y sugerencias del modelo."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.categorize import SUGGESTED, UNCATEGORIZED
from app.config import get_settings
from app.db import get_db
from app.deps import require_user, templates
from app.llm import OpenRouterError, suggest_categories
from app.models import CategoryRule, Transaction
from app.rules import apply_rule, recategorize, save_rule, uncategorized_descriptions

router = APIRouter(prefix="/categorias")


def _redirect(message: str = "", error: str = "") -> RedirectResponse:
    if error:
        destino = f"/categorias?error={quote(error)}"
    elif message:
        destino = f"/categorias?message={quote(message)}"
    else:
        destino = "/categorias"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _known_categories(db: Session) -> list[str]:
    usadas = {
        c
        for (c,) in db.execute(select(Transaction.category).distinct()).all()
        if c and c != UNCATEGORIZED
    }
    return sorted(usadas | set(SUGGESTED))


@router.get("", response_class=HTMLResponse)
def categories_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    message: str | None = None,
    error: str | None = None,
):
    pendientes = uncategorized_descriptions(db)
    reglas = (
        db.execute(select(CategoryRule).order_by(CategoryRule.created_at.desc())).scalars().all()
    )
    total = db.execute(select(func.count(Transaction.id))).scalar() or 0
    sin_categoria = (
        db.execute(
            select(func.count(Transaction.id)).where(Transaction.category == UNCATEGORIZED)
        ).scalar()
        or 0
    )
    return templates.TemplateResponse(
        request,
        "categories.html",
        {
            "pending": pendientes,
            "rules": reglas,
            "categories": _known_categories(db),
            "total": total,
            "uncategorized": sin_categoria,
            "chat_enabled": get_settings().chat_enabled,
            "message": message,
            "error": error,
            "active_page": "categories",
        },
    )


@router.post("/regla")
def create_rule(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    pattern: str = Form(...),
    category: str = Form(...),
    category_new: str = Form(""),
):
    categoria = (category_new or "").strip() or (category or "").strip()
    try:
        save_rule(db, pattern, categoria, source="manual")
    except ValueError as exc:
        return _redirect(error=str(exc))
    aplicados = apply_rule(db, pattern, categoria)
    return _redirect(
        message=f"'{pattern.strip()}' → {categoria}. Se actualizaron {aplicados} movimientos."
    )


@router.post("/regla/{rule_id}/borrar")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
):
    db.execute(delete(CategoryRule).where(CategoryRule.id == rule_id))
    db.commit()
    return _redirect(message="Regla borrada. Los movimientos ya categorizados no cambian.")


@router.post("/recategorizar")
def rerun(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    only_uncategorized: str = Form(""),
):
    solo_pendientes = only_uncategorized.lower() in {"1", "true", "on", "si"}
    cambiados = recategorize(db, only_uncategorized=solo_pendientes)
    if not cambiados:
        return _redirect(message="No hubo nada que cambiar.")
    return _redirect(message=f"Se recategorizaron {cambiados} movimientos.")


@router.post("/sugerir")
async def suggest(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
):
    """Le pide al modelo una categoria para cada descripcion sin categorizar."""
    pendientes = [descripcion for descripcion, _veces, _total in uncategorized_descriptions(db)]
    if not pendientes:
        return _redirect(message="No queda nada sin categorizar.")

    try:
        sugerencias = await suggest_categories(pendientes)
    except OpenRouterError as exc:
        return _redirect(error=f"No se pudieron pedir sugerencias: {exc}")

    creadas = 0
    aplicados = 0
    for descripcion, categoria in sugerencias.items():
        categoria = (categoria or "").strip()
        if not categoria or categoria == UNCATEGORIZED:
            continue
        try:
            save_rule(db, descripcion, categoria, source="ai")
        except ValueError:
            continue
        creadas += 1
        aplicados += apply_rule(db, descripcion, categoria)

    if not creadas:
        return _redirect(error="El modelo no pudo clasificar ninguna descripcion.")
    return _redirect(
        message=f"El modelo sugirio {creadas} categorias y se aplicaron a {aplicados} movimientos. "
        "Revisalas abajo: las que no te convenzan, borralas o corregilas."
    )

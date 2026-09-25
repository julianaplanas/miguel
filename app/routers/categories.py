"""Pantalla de categorias: reglas, recategorizado y sugerencias del modelo."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.categorize import NOT_A_MOVEMENT, SUGGESTED, UNCATEGORIZED, is_not_movement
from app.config import get_settings
from app.db import get_db
from app.deps import require_user, templates
from app.llm import OpenRouterError, suggest_categories
from app.models import CategoryRule, Transaction
from app.preferences import CAT_AI, categorization_mode
from app.rules import (
    ai_review_non_movements,
    apply_rule,
    delete_matching,
    description_summary,
    recategorize,
    save_rule,
    uncategorized_descriptions,
)

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
    ver: str = "pendientes",
):
    solo_pendientes = ver != "todas"
    descripciones = description_summary(db, only_uncategorized=solo_pendientes)
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
    chat_enabled = get_settings().chat_enabled
    modo = categorization_mode(db)
    return templates.TemplateResponse(
        request,
        "categories.html",
        {
            "descriptions": descripciones,
            "ver": "pendientes" if solo_pendientes else "todas",
            "mode": modo,
            "mode_is_ai": modo == CAT_AI and chat_enabled,
            "mode_reason": (
                "" if chat_enabled else "falta la variable OPENROUTER_API_KEY"
            ),
            "rules": reglas,
            "categories": _known_categories(db),
            "total": total,
            "uncategorized": sin_categoria,
            "chat_enabled": chat_enabled,
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
    descartados = 0
    for descripcion, categoria in sugerencias.items():
        categoria = (categoria or "").strip()
        if not categoria or categoria == UNCATEGORIZED:
            continue
        # El modelo tambien puede decir que la linea no es un movimiento
        # (un total, un saldo). Esa respuesta se guarda igual y las lineas
        # se borran.
        descartar = is_not_movement(categoria)
        try:
            save_rule(
                db, descripcion, NOT_A_MOVEMENT if descartar else categoria, source="ai"
            )
        except ValueError:
            continue
        creadas += 1
        if descartar:
            descartados += delete_matching(db, descripcion)
        else:
            aplicados += apply_rule(db, descripcion, categoria)

    if not creadas:
        return _redirect(error="El modelo no pudo clasificar ninguna descripcion.")
    mensaje = f"El modelo sugirio {creadas} categorias y se aplicaron a {aplicados} movimientos."
    if descartados:
        mensaje += (
            f" Ademas descarto {descartados} lineas que no son movimientos "
            "(totales, saldos y similares)."
        )
    return _redirect(
        message=mensaje + " Revisalas abajo: las que no te convenzan, borralas o corregilas."
    )


@router.post("/revisar-lineas")
async def review_lines(db: Session = Depends(get_db), _: str = Depends(require_user)):
    """Busca entre TODAS las descripciones las que no son movimientos.

    El paso de la importacion solo pregunta por lo que quedo sin categoria,
    asi que una linea de totales que ya tiene categoria no se revisa sola
    nunca. Esto es el repaso completo, a pedido.
    """
    if not get_settings().chat_enabled:
        return _redirect(error="No hay OPENROUTER_API_KEY configurada.")
    try:
        borrados, descartadas = await ai_review_non_movements(db)
    except OpenRouterError as exc:
        return _redirect(error=f"No se pudo revisar: {exc}")

    if not descartadas:
        return _redirect(message="Revisado: no hay lineas que sobren.")
    muestra = ", ".join(descartadas[:6])
    if len(descartadas) > 6:
        muestra += f" y {len(descartadas) - 6} mas"
    return _redirect(
        message=f"Se descartaron {borrados} movimientos de {len(descartadas)} descripciones: "
        f"{muestra}. Si alguna no correspondia, borra su regla abajo y volve a importar."
    )


@router.post("/probar")
async def probe(db: Session = Depends(get_db), _: str = Depends(require_user)):
    """Hace una llamada real al modelo y muestra que contesto o que fallo.

    Existe porque cuando la categorizacion "no anda" el motivo esta del otro
    lado de la red (clave mal puesta, modelo inexistente, sin credito) y sin
    esto el usuario no tiene forma de verlo.
    """
    settings = get_settings()
    if not settings.chat_enabled:
        return _redirect(error="No hay OPENROUTER_API_KEY configurada.")

    prueba = [{"descripcion": "COMPRA COTO DIGITAL", "importe": 48200.5, "es_gasto": True}]
    try:
        respuesta = await suggest_categories(prueba)
    except OpenRouterError as exc:
        return _redirect(error=f"Modelo '{settings.openrouter_model}': {exc}")

    categoria = respuesta.get("COMPRA COTO DIGITAL")
    if not categoria:
        return _redirect(
            error=f"El modelo '{settings.openrouter_model}' contesto, pero no devolvio "
            f"una categoria para la prueba. Respuesta: {respuesta}"
        )
    return _redirect(
        message=f"Conexion OK con '{settings.openrouter_model}'. "
        f"Para 'COMPRA COTO DIGITAL' respondio: {categoria}."
    )

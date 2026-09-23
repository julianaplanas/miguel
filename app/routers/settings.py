"""Ajustes: tipos de cambio hacia la moneda base."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import currency as cur
from app.analytics import available_options, base_currency
from app.config import get_settings
from app.db import get_db
from app.deps import require_user, templates
from app.models import ExchangeRate

router = APIRouter(prefix="/ajustes")


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    message: str | None = None,
    error: str | None = None,
):
    base = base_currency()
    opciones = available_options(db)
    guardados = {
        (r.code or "").upper(): r
        for r in db.execute(select(ExchangeRate)).scalars().all()
    }
    # Primero las monedas que aparecen en los datos, luego el resto conocidas.
    en_uso = [c for c in opciones["currencies"] if c != base]
    otras = [c for c in cur.known_codes() if c != base and c not in en_uso]

    def fila(code: str, usada: bool) -> dict:
        guardado = guardados.get(code)
        obsoleto = bool(guardado and guardado.base and guardado.base.upper() != base)
        return {
            "code": code,
            "label": cur.label(code),
            "rate": None if (guardado is None or obsoleto) else guardado.rate,
            "updated_at": None if guardado is None else guardado.updated_at,
            "stale_base": obsoleto,
            "in_use": usada,
        }

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "base": base,
            "base_label": cur.label(base),
            "rows": [fila(c, True) for c in en_uso] + [fila(c, False) for c in otras],
            "missing": opciones["missing_rates"],
            "message": message,
            "error": error,
            "active_page": "settings",
        },
    )


@router.post("/tipo-de-cambio")
def save_rate(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    code: str = Form(...),
    rate: str = Form(""),
):
    base = base_currency()
    code = cur.normalize_code(code, "").upper()
    if not code or code == base:
        return RedirectResponse(
            "/ajustes?error=Moneda no valida", status_code=status.HTTP_303_SEE_OTHER
        )

    texto = rate.replace(".", "").replace(",", ".").strip() if "," in rate else rate.strip()
    if not texto:
        db.execute(delete(ExchangeRate).where(ExchangeRate.code == code))
        db.commit()
        return RedirectResponse(
            f"/ajustes?message=Se borro el tipo de cambio de {code}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        valor = float(texto)
    except ValueError:
        return RedirectResponse(
            f"/ajustes?error='{rate}' no es un numero",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if valor <= 0:
        return RedirectResponse(
            "/ajustes?error=El tipo de cambio tiene que ser mayor que cero",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    existente = db.get(ExchangeRate, code)
    if existente:
        existente.rate = valor
        existente.base = base
    else:
        db.add(ExchangeRate(code=code, base=base, rate=valor))
    db.commit()
    return RedirectResponse(
        f"/ajustes?message=1 {code} = {valor:g} {base}",
        status_code=status.HTTP_303_SEE_OTHER,
    )

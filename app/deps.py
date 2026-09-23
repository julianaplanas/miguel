"""Dependencias compartidas: plantillas y control de acceso."""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.security import read_session

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _format_money(value: float | int | None, code: str | None = None) -> str:
    """Formato es-AR: 1.234,56 ARS. El codigo se puede pasar por fila."""
    if value is None:
        return "-"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} {(code or get_settings().currency).upper()}"


templates.env.filters["money"] = _format_money
templates.env.globals["settings"] = get_settings()


def require_user(request: Request) -> str:
    username = read_session(request)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    return username

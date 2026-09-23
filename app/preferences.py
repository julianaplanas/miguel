"""Preferencias guardadas en la base (lo que manda sobre las variables de entorno)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import currency as cur
from app.config import get_settings
from app.models import AppSetting

BASE_CURRENCY = "base_currency"


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row and row.value else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def base_currency(db: Session) -> str:
    """Moneda base: la guardada en Ajustes o, si no hay, la de CURRENCY."""
    guardada = get_setting(db, BASE_CURRENCY)
    if guardada:
        return guardada.upper()
    return get_settings().currency.upper()


def set_base_currency(db: Session, code: str) -> str:
    codigo = cur.normalize_code(code, "").upper()
    if not codigo:
        raise ValueError("Moneda no reconocida")
    set_setting(db, BASE_CURRENCY, codigo)
    return codigo

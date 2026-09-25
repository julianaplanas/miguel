"""Preferencias guardadas en la base (lo que manda sobre las variables de entorno)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import currency as cur
from app.config import get_settings
from app.models import AppSetting

BASE_CURRENCY = "base_currency"
CONVERSION_MODE = "conversion_mode"
CATEGORIZATION = "categorization"

CAT_AI = "ai"
CAT_RULES = "rules"

MODE_CURRENT = "current"
MODE_HISTORICAL = "historical"

PDF_READER = "pdf_reader"
# Cuando usar el modelo para leer un PDF. El lector automatico es gratis,
# instantaneo y comprobable, pero cada banco maqueta distinto; el modelo
# entiende cualquier maquetacion pero cuesta y tarda. Lo que decide es la
# aritmetica: si lo leido cuadra con los totales que declara el documento,
# no hace falta gastar nada.
PDF_AUTO = "auto"          # solo el lector automatico
PDF_FALLBACK = "fallback"  # el modelo cuando el automatico no cuadra
PDF_ALWAYS = "always"      # siempre el modelo
PDF_READERS = {PDF_AUTO, PDF_FALLBACK, PDF_ALWAYS}


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


def conversion_mode(db: Session) -> str:
    """'current' (tipo de hoy) o 'historical' (tipo del dia de cada gasto)."""
    valor = get_setting(db, CONVERSION_MODE, MODE_CURRENT)
    return valor if valor in {MODE_CURRENT, MODE_HISTORICAL} else MODE_CURRENT


def set_conversion_mode(db: Session, mode: str) -> str:
    if mode not in {MODE_CURRENT, MODE_HISTORICAL}:
        raise ValueError("Modo de conversion no valido")
    set_setting(db, CONVERSION_MODE, mode)
    return mode


def categorization_mode(db: Session) -> str:
    """'ai' (el modelo decide) o 'rules' (solo reglas, sin red).

    Por defecto 'ai' si hay clave de OpenRouter configurada. Las reglas
    siguen existiendo: guardan lo que el usuario corrige y cachean lo que
    el modelo ya respondio, para no volver a preguntarlo.
    """
    guardada = get_setting(db, CATEGORIZATION)
    if guardada in {CAT_AI, CAT_RULES}:
        return guardada
    return CAT_AI if get_settings().chat_enabled else CAT_RULES


def set_categorization_mode(db: Session, mode: str) -> str:
    if mode not in {CAT_AI, CAT_RULES}:
        raise ValueError("Estrategia de categorizacion no valida")
    set_setting(db, CATEGORIZATION, mode)
    return mode


def pdf_reader_mode(db: Session) -> str:
    """Cuando leer un PDF con el modelo: 'auto', 'fallback' o 'always'."""
    guardada = get_setting(db, PDF_READER)
    if guardada in PDF_READERS:
        return guardada
    return PDF_FALLBACK if get_settings().chat_enabled else PDF_AUTO


def set_pdf_reader_mode(db: Session, mode: str) -> str:
    if mode not in PDF_READERS:
        raise ValueError("Modo de lectura no valido")
    set_setting(db, PDF_READER, mode)
    return mode

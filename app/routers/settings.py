"""Ajustes: moneda base y tipos de cambio (a mano o desde una API)."""
from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import currency as cur
from app import rates as rate_api
from app.analytics import available_options, fetch_rows, Filters
from app.db import get_db
from app.deps import require_user, templates
from app.models import ExchangeRate, RateHistory
from app.config import get_settings
from app.preferences import (
    CAT_AI,
    CAT_RULES,
    MODE_CURRENT,
    MODE_HISTORICAL,
    PDF_ALWAYS,
    PDF_AUTO,
    PDF_FALLBACK,
    base_currency,
    categorization_mode,
    conversion_mode,
    pdf_reader_mode,
    set_base_currency,
    set_categorization_mode,
    set_conversion_mode,
    set_pdf_reader_mode,
)

router = APIRouter(prefix="/ajustes")


def _redirect(message: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import quote

    if error:
        destino = f"/ajustes?error={quote(error)}"
    elif message:
        destino = f"/ajustes?message={quote(message)}"
    else:
        destino = "/ajustes"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def _save(db: Session, code: str, base: str, rate: float, source: str) -> None:
    existente = db.get(ExchangeRate, code)
    if existente:
        existente.rate = rate
        existente.base = base
        existente.source = source
    else:
        db.add(ExchangeRate(code=code, base=base, rate=rate, source=source))
    db.commit()


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    message: str | None = None,
    error: str | None = None,
):
    base = base_currency(db)
    opciones = available_options(db)
    guardados = {
        (r.code or "").upper(): r for r in db.execute(select(ExchangeRate)).scalars().all()
    }
    en_uso = [c for c in opciones["currencies"] if c != base]
    otras = [c for c in cur.known_codes() if c != base and c not in en_uso]

    ahora = dt.datetime.now(dt.timezone.utc)

    # Cobertura del historico por moneda: cuantos dias y que periodo.
    cobertura: dict[str, dict] = {}
    for code, dias, desde, hasta in db.execute(
        select(
            RateHistory.code,
            func.count(RateHistory.date),
            func.min(RateHistory.date),
            func.max(RateHistory.date),
        )
        .where(RateHistory.base == base)
        .group_by(RateHistory.code)
    ).all():
        cobertura[(code or "").upper()] = {"days": dias, "from": desde, "to": hasta}

    def fila(code: str, usada: bool) -> dict:
        guardado = guardados.get(code)
        obsoleto = bool(guardado and guardado.base and guardado.base.upper() != base)
        actualizado = None if guardado is None else guardado.updated_at
        antiguedad = None
        if actualizado is not None:
            referencia = actualizado
            if referencia.tzinfo is None:
                referencia = referencia.replace(tzinfo=dt.timezone.utc)
            antiguedad = int((ahora - referencia).total_seconds() // 3600)
        return {
            "code": code,
            "label": cur.label(code),
            "rate": None if (guardado is None or obsoleto) else guardado.rate,
            "updated_at": actualizado,
            "hours_old": antiguedad,
            "source": (guardado.source if guardado else "") or rate_api.MANUAL,
            "source_label": rate_api.source_label(guardado.source if guardado else ""),
            "sources": rate_api.available_sources(code, base),
            "auto": bool(guardado and guardado.source and guardado.source != rate_api.MANUAL),
            "stale_base": obsoleto,
            "in_use": usada,
            "history": cobertura.get(code),
            "history_label": rate_api.history_label(
                rate_api.history_source(code, base, guardado.source if guardado else "")
            ),
        }

    filas = [fila(c, True) for c in en_uso] + [fila(c, False) for c in otras]
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "base": base,
            "base_label": cur.label(base),
            "base_options": cur.known_codes(),
            "rows": filas,
            "missing": opciones["missing_rates"],
            "mode": conversion_mode(db),
            "mode_current": MODE_CURRENT,
            "mode_historical": MODE_HISTORICAL,
            "period": _period(db),
            "storage": _storage_info(),
            "categorization": categorization_mode(db),
            "cat_ai": CAT_AI,
            "cat_rules": CAT_RULES,
            "pdf_reader": pdf_reader_mode(db),
            "pdf_auto": PDF_AUTO,
            "pdf_fallback": PDF_FALLBACK,
            "pdf_always": PDF_ALWAYS,
            "chat_enabled": get_settings().chat_enabled,
            "can_refresh_all": any(f["auto"] or f["in_use"] for f in filas),
            "message": message,
            "error": error,
            "active_page": "settings",
        },
    )


@router.post("/moneda-base")
def save_base(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    code: str = Form(...),
):
    try:
        nueva = set_base_currency(db, code)
    except ValueError:
        return _redirect(error=f"No reconozco la moneda '{code}'")
    return _redirect(
        message=f"Moneda base: {nueva}. Los tipos de cambio guardados contra otra base hay que volver a cargarlos."
    )


@router.post("/tipo-de-cambio")
def save_rate(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    code: str = Form(...),
    rate: str = Form(""),
    source: str = Form(rate_api.MANUAL),
):
    base = base_currency(db)
    code = cur.normalize_code(code, "").upper()
    if not code or code == base:
        return _redirect(error="Moneda no valida")

    texto = rate.strip()
    if not texto:
        db.execute(delete(ExchangeRate).where(ExchangeRate.code == code))
        db.commit()
        return _redirect(message=f"Se borro el tipo de cambio de {code}")

    # Aceptamos tanto 1234.56 como 1.234,56
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        return _redirect(error=f"'{rate}' no es un numero")
    if valor <= 0:
        return _redirect(error="El tipo de cambio tiene que ser mayor que cero")

    _save(db, code, base, valor, source or rate_api.MANUAL)
    return _redirect(message=f"1 {code} = {valor:g} {base}")


@router.post("/cotizacion")
def refresh_rate(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    code: str = Form(...),
    source: str = Form(""),
):
    base = base_currency(db)
    code = cur.normalize_code(code, "").upper()
    if not code or code == base:
        return _redirect(error="Moneda no valida")
    try:
        obtenida = rate_api.fetch_rate(code, base, source)
    except rate_api.RateError as exc:
        # El valor anterior se queda como estaba.
        return _redirect(error=f"No se pudo traer la cotizacion de {code}: {exc}")
    _save(db, code, base, obtenida.rate, obtenida.source)
    detalle = f" ({obtenida.detail})" if obtenida.detail else ""
    return _redirect(message=f"1 {code} = {obtenida.rate:g} {base}{detalle}")


@router.post("/cotizaciones")
def refresh_all(db: Session = Depends(get_db), _: str = Depends(require_user)):
    """Actualiza las monedas que aparecen en los datos y no son manuales."""
    base = base_currency(db)
    presentes = [c for c in available_options(db)["currencies"] if c != base]
    guardados = {
        (r.code or "").upper(): r for r in db.execute(select(ExchangeRate)).scalars().all()
    }

    actualizadas: list[str] = []
    fallos: list[str] = []
    for code in presentes:
        guardado = guardados.get(code)
        # Un origen vacio (o nulo, en filas de antes de que existiera la
        # columna) significa cargado a mano: no lo pisamos.
        origen = (guardado.source if guardado else "") or rate_api.MANUAL
        if origen == rate_api.MANUAL and guardado is not None:
            continue
        try:
            obtenida = rate_api.fetch_rate(code, base, origen)
        except rate_api.RateError as exc:
            fallos.append(f"{code}: {exc}")
            continue
        _save(db, code, base, obtenida.rate, obtenida.source)
        actualizadas.append(f"1 {code} = {obtenida.rate:g} {base}")

    if fallos and not actualizadas:
        return _redirect(error=" · ".join(fallos))
    if not actualizadas:
        return _redirect(message="No habia ninguna moneda con cotizacion automatica que actualizar.")
    mensaje = "Actualizado: " + " · ".join(actualizadas)
    if fallos:
        mensaje += f". Fallaron: {' · '.join(fallos)}"
    return _redirect(message=mensaje)


def _period(db: Session) -> tuple[dt.date, dt.date] | None:
    """Periodo que cubren los movimientos activos (para pedir el historico)."""
    fechas = [r["date"] for r in fetch_rows(db, Filters()) if r["date"]]
    if not fechas:
        return None
    return min(fechas), max(fechas)


@router.post("/modo-conversion")
def save_mode(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    mode: str = Form(...),
):
    try:
        elegido = set_conversion_mode(db, mode)
    except ValueError:
        return _redirect(error="Modo de conversion no valido")
    if elegido == MODE_HISTORICAL:
        return _redirect(
            message="Cada movimiento se convierte al tipo de cambio de su fecha. "
            "Descarga el historico de las monedas que uses."
        )
    return _redirect(message="Todo se convierte al tipo de cambio actual.")


@router.post("/historico")
def download_history(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    code: str = Form(...),
):
    base = base_currency(db)
    code = cur.normalize_code(code, "").upper()
    if not code or code == base:
        return _redirect(error="Moneda no valida")

    periodo = _period(db)
    if periodo is None:
        return _redirect(error="No hay movimientos con fecha: no se que periodo pedir.")
    desde, hasta = periodo
    # Un poco de margen hacia atras, para que el primer movimiento tenga
    # una cotizacion anterior a la que agarrarse.
    desde = desde - dt.timedelta(days=10)

    guardado = db.get(ExchangeRate, code)
    origen_actual = (guardado.source if guardado else "") or ""
    try:
        serie, origen = rate_api.fetch_history(code, base, desde, hasta, origen_actual)
    except rate_api.RateError as exc:
        return _redirect(error=f"No se pudo traer el historico de {code}: {exc}")

    db.execute(
        delete(RateHistory).where(RateHistory.code == code, RateHistory.base == base)
    )
    db.add_all(
        [
            RateHistory(code=code, base=base, date=fecha, rate=valor, source=origen)
            for fecha, valor in serie
        ]
    )
    db.commit()
    return _redirect(
        message=f"{code}: {len(serie)} cotizaciones entre {serie[0][0]} y {serie[-1][0]} "
        f"({rate_api.history_label(origen)})"
    )


@router.post("/historico/borrar")
def clear_history(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    code: str = Form(...),
):
    base = base_currency(db)
    code = cur.normalize_code(code, "").upper()
    db.execute(delete(RateHistory).where(RateHistory.code == code, RateHistory.base == base))
    db.commit()
    return _redirect(message=f"Se borro el historico de {code}")


@router.post("/categorizacion")
def save_categorization(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    mode: str = Form(...),
):
    try:
        elegido = set_categorization_mode(db, mode)
    except ValueError:
        return _redirect(error="Estrategia no valida")
    if elegido == CAT_AI:
        return _redirect(
            message="Las categorias las decide el modelo. Cada descripcion se pregunta una "
            "sola vez y la respuesta queda guardada como regla."
        )
    return _redirect(message="Las categorias salen solo de las reglas, sin consultar al modelo.")


@router.post("/lector-pdf")
def save_pdf_reader(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    mode: str = Form(...),
):
    try:
        elegido = set_pdf_reader_mode(db, mode)
    except ValueError:
        return _redirect(error="Modo de lectura no valido")
    mensajes = {
        PDF_AUTO: "Los PDF los lee solo el lector automatico.",
        PDF_FALLBACK: "Si la lectura automatica no cuadra con los totales del resumen, "
        "la hace el modelo.",
        PDF_ALWAYS: "Los PDF los lee siempre el modelo. Cuesta una llamada por hoja.",
    }
    return _redirect(message=mensajes[elegido])


def _storage_info() -> dict:
    """Donde se guardan los datos y si eso sobrevive a un deploy."""
    settings = get_settings()
    data_dir = settings.data_dir
    uploads = settings.upload_dir

    archivos = 0
    tamano = 0
    if uploads.exists():
        for item in uploads.iterdir():
            if item.is_file():
                archivos += 1
                tamano += item.stat().st_size

    escribible = False
    try:
        uploads.mkdir(parents=True, exist_ok=True)
        prueba = uploads / ".escritura"
        prueba.write_text("ok")
        prueba.unlink()
        escribible = True
    except OSError:
        escribible = False

    libre = None
    try:
        libre = shutil.disk_usage(data_dir).free
    except OSError:
        pass

    base_datos = (
        "Postgres (DATABASE_URL)"
        if os.getenv("DATABASE_URL", "").strip()
        else str(Path(settings.database_url.replace("sqlite:///", "")))
    )

    return {
        "data_dir": str(data_dir),
        "source": settings.data_dir_source,
        "volume": settings.volume_mount or "",
        "on_railway": settings.on_railway,
        "persistent": settings.data_is_persistent,
        "warning": settings.storage_warning,
        "files": archivos,
        "size_mb": round(tamano / (1024 * 1024), 2),
        "writable": escribible,
        "free_mb": None if libre is None else round(libre / (1024 * 1024), 1),
        "database": base_datos,
    }

"""Gestion de archivos: subida, activacion/desactivacion, remapeo y borrado."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import currency as cur
from app.config import get_settings
from app.deps import require_user, templates
from app.llm import OpenRouterError, find_non_movements
from app.preferences import CAT_AI, base_currency, categorization_mode
from app.categorize import NOT_A_MOVEMENT, SOURCE_AI, is_not_movement
from app.rules import (
    ai_categorize_pending,
    save_rule,
    categorize_rows,
    drop_non_movements,
    load_rules,
    recategorize,
)
from app.ingest import FIELDS, parse_file
from app.db import get_db
from app.models import Transaction, UploadedFile

router = APIRouter(prefix="/archivos")

ALLOWED_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".pdf"}


def _stored_file(record: UploadedFile) -> Path | None:
    """Ruta real del archivo guardado, o None si ya no esta.

    Si la carpeta de datos cambio (por ejemplo al montar el volumen), la
    ruta absoluta guardada ya no sirve pero el archivo puede seguir ahi
    con el mismo nombre.
    """
    path = Path(record.stored_path)
    if path.exists():
        return path
    alternativa = get_settings().upload_dir / path.name
    return alternativa if alternativa.exists() else None


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato no soportado ({suffix or 'sin extension'}). Usa CSV, TSV, Excel o PDF.",
        )
    return suffix


def _import_rows(db: Session, record: UploadedFile, parsed) -> None:
    base = base_currency(db)
    # Un extracto bancario no trae categoria: se deduce de la descripcion.
    # Con el modelo activado se consultan primero las reglas guardadas (lo
    # que el usuario corrigio y lo que el modelo ya respondio antes) y el
    # resto se le pregunta al modelo despues de guardar; sin modelo, se usan
    # tambien las reglas de fabrica.
    con_ia = categorization_mode(db) == CAT_AI and get_settings().chat_enabled
    categorizadas = categorize_rows(parsed.rows, load_rules(db, include_defaults=not con_ia))

    # Lo que ya se decidio antes que no era un movimiento (un total, un
    # saldo) no vuelve a entrar: la decision esta guardada como regla, asi
    # que reimportar el archivo no lo resucita.
    descartadas = [r for r in parsed.rows if is_not_movement(r.get("category"))]
    if descartadas:
        parsed.rows[:] = [r for r in parsed.rows if not is_not_movement(r.get("category"))]

    db.execute(delete(Transaction).where(Transaction.file_id == record.id))
    db.add_all(
        [
            Transaction(
                file_id=record.id,
                date=row["date"],
                description=row["description"],
                amount=row["amount"],
                currency=(row["currency"] or base).upper(),
                category=row["category"] or "Sin categoria",
                category_source=row.get("category_source", ""),
                person=row["person"] or "Sin asignar",
                account=row["account"],
                raw=row["raw"],
            )
            for row in parsed.rows
        ]
    )
    record.row_count = len(parsed.rows)
    record.column_mapping = json.dumps(parsed.mapping, ensure_ascii=False)
    record.detected_columns = json.dumps(parsed.columns, ensure_ascii=False)
    avisos = list(parsed.warnings)
    if descartadas:
        avisos.append(f"Se descartaron {len(descartadas)} lineas que no son movimientos.")
    record.notes = " ".join(avisos)[:1000]


def _descartadas(cuantas: int) -> str:
    if not cuantas:
        return ""
    return (
        f" Se descartaron {cuantas} lineas que no son movimientos "
        "(totales, saldos y similares)."
    )


async def _categorize_after_import(db: Session) -> str:
    """Categoriza lo que quedo pendiente y devuelve un resumen para el aviso."""
    if categorization_mode(db) != CAT_AI or not get_settings().chat_enabled:
        pendientes = recategorize(db, only_uncategorized=True)
        fuera = drop_non_movements(db)
        aviso = f". Se categorizaron {pendientes} por reglas." if pendientes else ""
        return aviso + _descartadas(fuera)

    try:
        creadas, aplicados, descartados = await ai_categorize_pending(db)
    except OpenRouterError as exc:
        # Que falle el modelo no puede tumbar la importacion: los
        # movimientos ya estan guardados. Se cae a las reglas de fabrica.
        porreglas = recategorize(db, only_uncategorized=True)
        aviso = f". No se pudo categorizar con IA ({exc})"
        return aviso + (f"; se usaron las reglas para {porreglas}." if porreglas else ".")

    # Lo que el modelo no supo clasificar se intenta con las reglas.
    porreglas = recategorize(db, only_uncategorized=True)
    partes = []
    if aplicados:
        partes.append(f"{aplicados} categorizados por el modelo")
    if porreglas:
        partes.append(f"{porreglas} por reglas")
    aviso = f". {' y '.join(partes)}." if partes else ""
    return aviso + _descartadas(descartados)


@router.get("", response_class=HTMLResponse)
def files_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    message: str | None = None,
    error: str | None = None,
):
    records = db.execute(select(UploadedFile).order_by(UploadedFile.uploaded_at.desc())).scalars().all()
    files = []
    for record in records:
        files.append(
            {
                "obj": record,
                "mapping": json.loads(record.column_mapping or "{}"),
                "columns": json.loads(record.detected_columns or "[]"),
            }
        )
    return templates.TemplateResponse(
        request,
        "files.html",
        {
            "files": files,
            "fields": FIELDS,
            "storage_warning": get_settings().storage_warning,
            "currencies": cur.known_codes(),
            "currency_label": cur.label,
            "base_currency": base_currency(db),
            "message": message,
            "error": error,
            "active_page": "files",
        },
    )


@router.post("/upload")
async def upload(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    files: list[UploadFile] = File(...),
    default_person: str = Form(""),
    default_currency: str = Form(""),
    revisar: str = Form(""),
):
    """Sube uno o varios archivos y los lee.

    Con `revisar` el archivo queda leido pero SIN importar, y se va a la
    pantalla de revision: ahi se ve fila por fila que se saco del documento
    antes de que entre en los totales. Sin `revisar` se importa directo,
    que es lo comodo cuando ya sabes como sale ese formato.

    Cada archivo se procesa por separado y se confirma en cuanto sale bien,
    asi que uno que falle no se lleva puestos los demas. Las categorias se
    piden una sola vez al final, para no hacer una llamada por archivo.
    """
    con_revision = revisar.strip().lower() in {"1", "true", "on", "si"}
    settings = get_settings()
    persona = default_person.strip()
    moneda = default_currency.strip() or base_currency(db)

    importados: list[UploadedFile] = []
    fallos: list[str] = []

    for archivo in files:
        nombre = archivo.filename or "sin nombre"
        stored_path: Path | None = None
        try:
            suffix = _safe_suffix(nombre)
            content = await archivo.read()
            if not content:
                raise ValueError("el archivo esta vacio")
            if len(content) > settings.max_upload_mb * 1024 * 1024:
                raise ValueError(f"supera el limite de {settings.max_upload_mb} MB")

            stored_path = settings.upload_dir / f"{uuid.uuid4().hex}{suffix}"
            stored_path.write_bytes(content)

            record = UploadedFile(
                filename=nombre,
                stored_path=str(stored_path),
                content_type=archivo.content_type or "",
                size_bytes=len(content),
                is_active=True,
                default_person=persona,
            )
            db.add(record)
            db.flush()

            parsed = parse_file(
                stored_path,
                default_person=persona,
                default_currency=moneda,
                raw=content,
            )
            record.column_mapping = json.dumps(parsed.mapping, ensure_ascii=False)
            record.detected_columns = json.dumps(parsed.columns, ensure_ascii=False)
            if con_revision:
                # Se guarda lo leido para la pantalla de revision, pero no
                # se crea ningun movimiento hasta que se confirme.
                record.row_count = parsed.row_count
                record.notes = " ".join(parsed.warnings)[:1000]
                record.imported = False
            else:
                _import_rows(db, record, parsed)
                record.imported = True
            db.commit()
            importados.append(record)
        except HTTPException as exc:
            db.rollback()
            if stored_path:
                stored_path.unlink(missing_ok=True)
            fallos.append(f"{nombre} ({exc.detail})")
        except Exception as exc:  # noqa: BLE001 - el motivo se le muestra al usuario
            db.rollback()
            if stored_path:
                stored_path.unlink(missing_ok=True)
            fallos.append(f"{nombre} ({exc})")

    if not importados:
        detalle = "; ".join(fallos) or "no se recibio ningun archivo"
        return RedirectResponse(
            f"/archivos?error={quote('No se pudo importar: ' + detalle)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    movimientos = sum(r.row_count for r in importados)
    if con_revision:
        if len(importados) == 1:
            mensaje = f"'{importados[0].filename}': {movimientos} filas leidas. Revisalas antes de importar."
        else:
            mensaje = (
                f"{len(importados)} archivos leidos ({movimientos} filas). "
                "Revisalos uno por uno antes de importar."
            )
        if fallos:
            mensaje += " No se pudieron leer: " + "; ".join(fallos) + "."
        return RedirectResponse(
            f"/archivos/{importados[0].id}/revisar?message={quote(mensaje)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if len(importados) == 1:
        mensaje = f"Se importaron {movimientos} filas de '{importados[0].filename}'"
    else:
        mensaje = f"Se importaron {len(importados)} archivos ({movimientos} movimientos)"
    mensaje += await _categorize_after_import(db)
    if fallos:
        mensaje += " No se pudieron importar: " + "; ".join(fallos) + "."

    return RedirectResponse(
        f"/archivos?message={quote(mensaje)}", status_code=status.HTTP_303_SEE_OTHER
    )


async def _reimportar(db: Session, record: UploadedFile) -> str:
    """Vuelve a leer el archivo guardado con el codigo y el mapeo de ahora.

    El archivo original se guarda justamente para esto: cuando mejora el
    lector (un banco nuevo, un formato de fecha que antes no entendia) no
    hace falta borrar y volver a subir.
    """
    path = _stored_file(record)
    if path is None:
        return "el archivo original ya no esta guardado"
    mapping = json.loads(record.column_mapping or "{}")
    try:
        parsed = parse_file(
            path,
            mapping=mapping or None,
            default_person=record.default_person or "",
            default_currency=mapping.get("default_currency") or base_currency(db),
        )
        _import_rows(db, record, parsed)
        record.imported = True
        db.commit()
    except Exception as exc:  # noqa: BLE001 - el motivo se le muestra al usuario
        db.rollback()
        return str(exc)
    return ""


def _mapping_from_form(valores: dict[str, str], base: str) -> dict:
    """Mapeo de columnas tal como viene del formulario de revision."""
    mapping = {campo: valor for campo, valor in valores.items() if campo in FIELDS and valor}
    mapping["invert_sign"] = str(valores.get("invert_sign", "")).lower() in {"1", "true", "on", "si"}
    mapping["default_currency"] = cur.normalize_code(valores.get("default_currency"), base)
    return mapping


def _preview(db: Session, record: UploadedFile, mapping: dict | None, persona: str, moneda: str):
    """Lee el archivo y devuelve las filas tal como entrarian, sin guardarlas.

    Es la misma lectura que hace la importacion: lo que se ve aca es
    exactamente lo que se va a guardar, no una aproximacion.
    """
    path = _stored_file(record)
    if path is None:
        raise ValueError("el archivo original ya no esta guardado")
    parsed = parse_file(
        path,
        mapping=mapping or None,
        default_person=persona,
        default_currency=moneda,
    )
    con_ia = categorization_mode(db) == CAT_AI and get_settings().chat_enabled
    categorize_rows(parsed.rows, load_rules(db, include_defaults=not con_ia))
    return parsed


def _preview_context(db: Session, record: UploadedFile, parsed, excluidas: set[int]) -> dict:
    filas = []
    por_moneda: dict[str, dict] = {}
    for indice, row in enumerate(parsed.rows):
        descartada = is_not_movement(row.get("category"))
        fuera = descartada or indice in excluidas
        filas.append(
            {
                "i": indice,
                "fecha": row["date"].isoformat() if row.get("date") else "",
                "descripcion": row.get("description") or "",
                "importe": row.get("amount") or 0.0,
                "moneda": (row.get("currency") or "").upper(),
                "categoria": "" if descartada else (row.get("category") or ""),
                "motivo": "una regla dice que no es un movimiento" if descartada else "",
                "incluida": not fuera,
            }
        )
        if fuera:
            continue
        codigo = (row.get("currency") or "").upper() or "?"
        acumulado = por_moneda.setdefault(codigo, {"code": codigo, "gasto": 0.0, "ingreso": 0.0, "filas": 0})
        importe = float(row.get("amount") or 0.0)
        acumulado["filas"] += 1
        if importe >= 0:
            acumulado["gasto"] += importe
        else:
            acumulado["ingreso"] += -importe

    fechas = [f["fecha"] for f in filas if f["incluida"] and f["fecha"]]
    pendientes = (
        db.execute(
            select(UploadedFile)
            .where(UploadedFile.imported.is_(False))
            .where(UploadedFile.id != record.id)
            .order_by(UploadedFile.uploaded_at)
        )
        .scalars()
        .all()
    )
    return {
        "file": record,
        "rows": filas,
        "incluidas": sum(1 for f in filas if f["incluida"]),
        "por_moneda": sorted(por_moneda.values(), key=lambda m: m["code"]),
        "desde": min(fechas) if fechas else "",
        "hasta": max(fechas) if fechas else "",
        "warnings": parsed.warnings,
        "skipped": parsed.skipped,
        "mapping": parsed.mapping,
        "columns": parsed.columns,
        "fields": FIELDS,
        "currencies": cur.known_codes(),
        "currency_label": cur.label,
        "base_currency": base_currency(db),
        "pendientes": pendientes,
        "chat_enabled": get_settings().chat_enabled,
        "active_page": "files",
    }


@router.get("/{file_id}/revisar", response_class=HTMLResponse)
def review(
    request: Request,
    file_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    message: str | None = None,
    error: str | None = None,
):
    """Muestra fila por fila lo que se saco del documento, antes de importar."""
    record = db.get(UploadedFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    mapping = json.loads(record.column_mapping or "{}")
    try:
        parsed = _preview(
            db,
            record,
            mapping,
            record.default_person or "",
            mapping.get("default_currency") or base_currency(db),
        )
    except Exception as exc:  # noqa: BLE001 - el motivo se le muestra al usuario
        return RedirectResponse(
            f"/archivos?error={quote(f'No se pudo leer {record.filename}: {exc}')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    contexto = _preview_context(db, record, parsed, set())
    contexto["message"] = message
    contexto["error"] = error
    return templates.TemplateResponse(request, "review.html", contexto)


@router.post("/{file_id}/revisar")
async def review_action(
    request: Request,
    file_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
):
    """Vuelve a leer con otro mapeo, pide ayuda al modelo, o importa.

    Las tres acciones comparten el formulario: lo que se ve en pantalla es
    el resultado de la misma lectura que va a guardar el boton de importar.
    """
    record = db.get(UploadedFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    form = await request.form()
    accion = str(form.get("accion") or "importar")
    persona = str(form.get("default_person") or "").strip()
    base = base_currency(db)
    mapping = _mapping_from_form({k: str(v) for k, v in form.items()}, base)
    moneda = mapping["default_currency"]
    excluidas = {int(v) for v in form.getlist("excluir") if str(v).isdigit()}

    record.default_person = persona
    record.column_mapping = json.dumps(mapping, ensure_ascii=False)

    try:
        parsed = _preview(db, record, mapping, persona, moneda)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return RedirectResponse(
            f"/archivos?error={quote(f'No se pudo leer {record.filename}: {exc}')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    aviso = ""
    if accion == "modelo":
        # Se le pregunta al modelo cuales de estas lineas no son movimientos.
        # La respuesta queda como regla, asi que al volver a leer ya vienen
        # marcadas y no se vuelve a preguntar por ellas nunca mas.
        if not get_settings().chat_enabled:
            aviso = "No hay OPENROUTER_API_KEY configurada."
        else:
            vistas: dict[str, dict] = {}
            for row in parsed.rows:
                clave = (row.get("description") or "").strip()
                if not clave:
                    continue
                entrada = vistas.setdefault(clave, {"descripcion": clave, "importe": 0.0, "veces": 0})
                entrada["veces"] += 1
                entrada["importe"] = abs(float(row.get("amount") or 0.0))
            try:
                fuera = await find_non_movements(list(vistas.values()))
            except OpenRouterError as exc:
                aviso = f"El modelo no pudo revisar: {exc}"
                fuera = []
            nuevas = 0
            for descripcion in fuera:
                if descripcion not in vistas:
                    continue
                try:
                    save_rule(db, descripcion, NOT_A_MOVEMENT, source=SOURCE_AI)
                    nuevas += 1
                except ValueError:
                    continue
            db.commit()
            if nuevas:
                aviso = f"El modelo marco {nuevas} descripciones que no son movimientos."
            elif not aviso:
                aviso = "El modelo no encontro lineas que sobren."
            parsed = _preview(db, record, mapping, persona, moneda)
            excluidas = set()

    if accion in {"leer", "modelo"}:
        # Se vuelve a la misma pantalla por redirect y no devolviendo el
        # HTML del POST: asi refrescar no reenvia el formulario.
        db.commit()
        texto = aviso or ("Releido con el mapeo nuevo." if accion == "leer" else "")
        destino = f"/archivos/{record.id}/revisar"
        if texto:
            destino += f"?message={quote(texto)}"
        return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)

    # Importar: se quitan las filas desmarcadas y las que una regla descarta.
    quitadas = 0
    conservadas = []
    for indice, row in enumerate(parsed.rows):
        if indice in excluidas or is_not_movement(row.get("category")):
            quitadas += 1
            continue
        conservadas.append(row)
    parsed.rows[:] = conservadas

    _import_rows(db, record, parsed)
    record.imported = True
    db.commit()

    mensaje = f"'{record.filename}': {record.row_count} movimientos importados"
    if quitadas:
        mensaje += f" ({quitadas} lineas dejadas afuera)"
    mensaje += await _categorize_after_import(db)

    siguiente = (
        db.execute(
            select(UploadedFile)
            .where(UploadedFile.imported.is_(False))
            .order_by(UploadedFile.uploaded_at)
        )
        .scalars()
        .first()
    )
    if siguiente:
        return RedirectResponse(
            f"/archivos/{siguiente.id}/revisar?message={quote(mensaje + ' Sigue el proximo archivo.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        f"/archivos?message={quote(mensaje)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/reprocesar")
async def reprocess_all(db: Session = Depends(get_db), _: str = Depends(require_user)):
    """Reprocesa todos los archivos con el lector actual."""
    records = db.execute(select(UploadedFile)).scalars().all()
    if not records:
        return RedirectResponse(
            "/archivos?error=No hay archivos para reprocesar",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    hechos: list[UploadedFile] = []
    fallos: list[str] = []
    for record in records:
        error = await _reimportar(db, record)
        if error:
            fallos.append(f"{record.filename} ({error})")
        else:
            hechos.append(record)

    if not hechos:
        detalle = "; ".join(fallos)
        return RedirectResponse(
            f"/archivos?error={quote('No se pudo reprocesar: ' + detalle)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    movimientos = sum(r.row_count for r in hechos)
    if len(hechos) == 1:
        mensaje = f"Se reproceso '{hechos[0].filename}' ({movimientos} movimientos)"
    else:
        mensaje = f"Se reprocesaron {len(hechos)} archivos ({movimientos} movimientos)"
    mensaje += await _categorize_after_import(db)
    if fallos:
        mensaje += " No se pudieron reprocesar: " + "; ".join(fallos) + "."
    return RedirectResponse(
        f"/archivos?message={quote(mensaje)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{file_id}/reprocesar")
async def reprocess_one(
    file_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
):
    record = db.get(UploadedFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    nombre = record.filename
    error = await _reimportar(db, record)
    if error:
        return RedirectResponse(
            f"/archivos?error={quote(f'No se pudo reprocesar {nombre}: {error}')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    mensaje = f"'{nombre}': {record.row_count} movimientos"
    mensaje += await _categorize_after_import(db)
    return RedirectResponse(
        f"/archivos?message={quote(mensaje)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{file_id}/toggle")
def toggle(
    file_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    redirect_to: str = Form("/archivos"),
):
    record = db.get(UploadedFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    record.is_active = not record.is_active
    db.commit()
    target = redirect_to if redirect_to.startswith("/") else "/archivos"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{file_id}/remap")
async def remap(
    request: Request,
    file_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    date: str = Form(""),
    amount: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    person: str = Form(""),
    account: str = Form(""),
    currency: str = Form(""),
    kind: str = Form(""),
    invert_sign: str = Form(""),
    default_person: str = Form(""),
    default_currency: str = Form(""),
):
    record = db.get(UploadedFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    mapping = {
        key: value
        for key, value in {
            "date": date,
            "amount": amount,
            "description": description,
            "category": category,
            "person": person,
            "account": account,
            "currency": currency,
            "kind": kind,
        }.items()
        if value
    }
    mapping["invert_sign"] = invert_sign.lower() in {"1", "true", "on", "si"}
    mapping["default_currency"] = cur.normalize_code(default_currency, base_currency(db))

    path = _stored_file(record)
    if path is None:
        return RedirectResponse(
            "/archivos?error=El archivo original ya no esta disponible; vuelve a subirlo.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        parsed = parse_file(
            path,
            mapping=mapping,
            default_person=default_person.strip(),
            default_currency=mapping["default_currency"],
        )
        record.default_person = default_person.strip()
        _import_rows(db, record, parsed)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return RedirectResponse(
            f"/archivos?error=No se pudo reimportar: {exc}", status_code=status.HTTP_303_SEE_OTHER
        )
    db.commit()
    mensaje = f"Reimportado: {record.row_count} filas"
    mensaje += await _categorize_after_import(db)
    return RedirectResponse(
        f"/archivos?message={mensaje}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{file_id}/delete")
def delete_file(
    file_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
):
    record = db.get(UploadedFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    guardado = _stored_file(record)
    if guardado is not None:
        guardado.unlink(missing_ok=True)
    db.execute(delete(Transaction).where(Transaction.file_id == record.id))
    db.delete(record)
    db.commit()
    return RedirectResponse("/archivos?message=Archivo eliminado", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{file_id}/descargar")
def download(file_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)):
    record = db.get(UploadedFile, file_id)
    guardado = _stored_file(record) if record else None
    if not record or guardado is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(guardado, filename=record.filename)

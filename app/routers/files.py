"""Gestion de archivos: subida, activacion/desactivacion, remapeo y borrado."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import require_user, templates
from app.ingest import FIELDS, parse_file
from app.db import get_db
from app.models import Transaction, UploadedFile

router = APIRouter(prefix="/archivos")

ALLOWED_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato no soportado ({suffix or 'sin extension'}). Usa CSV, TSV o Excel.",
        )
    return suffix


def _import_rows(db: Session, record: UploadedFile, parsed) -> None:
    db.execute(delete(Transaction).where(Transaction.file_id == record.id))
    db.add_all(
        [
            Transaction(
                file_id=record.id,
                date=row["date"],
                description=row["description"],
                amount=row["amount"],
                currency=row["currency"] or get_settings().currency,
                category=row["category"] or "Sin categoria",
                person=row["person"] or "Sin asignar",
                account=row["account"],
                raw=row["raw"],
            )
            for row in parsed.rows
        ]
    )
    record.row_count = parsed.row_count
    record.column_mapping = json.dumps(parsed.mapping, ensure_ascii=False)
    record.detected_columns = json.dumps(parsed.columns, ensure_ascii=False)
    record.notes = " ".join(parsed.warnings)[:1000]


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
    file: UploadFile = File(...),
    default_person: str = Form(""),
):
    settings = get_settings()
    suffix = _safe_suffix(file.filename or "")
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        return RedirectResponse(
            f"/archivos?error=El archivo supera el limite de {settings.max_upload_mb} MB",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = settings.upload_dir / stored_name
    stored_path.write_bytes(content)

    record = UploadedFile(
        filename=file.filename or stored_name,
        stored_path=str(stored_path),
        content_type=file.content_type or "",
        size_bytes=len(content),
        is_active=True,
        default_person=default_person.strip(),
    )
    db.add(record)
    db.flush()

    try:
        parsed = parse_file(stored_path, default_person=default_person.strip(), raw=content)
        _import_rows(db, record, parsed)
    except Exception as exc:  # noqa: BLE001 - mostramos el motivo al usuario
        db.rollback()
        stored_path.unlink(missing_ok=True)
        return RedirectResponse(
            f"/archivos?error=No se pudo procesar '{file.filename}': {exc}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db.commit()
    return RedirectResponse(
        f"/archivos?message=Se importaron {record.row_count} filas de '{record.filename}'",
        status_code=status.HTTP_303_SEE_OTHER,
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
def remap(
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

    path = Path(record.stored_path)
    if not path.exists():
        return RedirectResponse(
            f"/archivos?error=El archivo original ya no esta disponible; vuelve a subirlo.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        parsed = parse_file(path, mapping=mapping, default_person=default_person.strip())
        record.default_person = default_person.strip()
        _import_rows(db, record, parsed)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return RedirectResponse(
            f"/archivos?error=No se pudo reimportar: {exc}", status_code=status.HTTP_303_SEE_OTHER
        )
    db.commit()
    return RedirectResponse(
        f"/archivos?message=Reimportado: {record.row_count} filas",
        status_code=status.HTTP_303_SEE_OTHER,
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
    Path(record.stored_path).unlink(missing_ok=True)
    db.execute(delete(Transaction).where(Transaction.file_id == record.id))
    db.delete(record)
    db.commit()
    return RedirectResponse("/archivos?message=Archivo eliminado", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{file_id}/descargar")
def download(file_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)):
    record = db.get(UploadedFile, file_id)
    if not record or not Path(record.stored_path).exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(record.stored_path, filename=record.filename)

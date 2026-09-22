"""Dashboard y API de datos agregados."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import Filters, available_options, build_summary, fetch_rows, parse_date
from app.db import get_db
from app.deps import require_user, templates
from app.models import UploadedFile

router = APIRouter()


def _filters(
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    persona: list[str] | None = Query(None),
    categoria: list[str] | None = Query(None),
    archivo: list[int] | None = Query(None),
) -> Filters:
    return Filters(
        date_from=parse_date(desde),
        date_to=parse_date(hasta),
        persons=[p for p in (persona or []) if p],
        categories=[c for c in (categoria or []) if c],
        file_ids=archivo or [],
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_user)):
    files = db.execute(select(UploadedFile).order_by(UploadedFile.uploaded_at.desc())).scalars().all()
    active = [f for f in files if f.is_active]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "files": files,
            "active_count": len(active),
            "total_files": len(files),
            "options": available_options(db),
            "active_page": "dashboard",
        },
    )


@router.get("/api/resumen")
def api_summary(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    filters: Filters = Depends(_filters),
):
    data = build_summary(db, filters)
    data["options"] = available_options(db)
    return data


@router.get("/api/opciones")
def api_options(db: Session = Depends(get_db), _: str = Depends(require_user)):
    return available_options(db)


@router.get("/api/transacciones")
def api_transactions(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    filters: Filters = Depends(_filters),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    orden: str = Query("fecha"),
):
    rows = fetch_rows(db, filters)
    if orden == "importe":
        rows.sort(key=lambda r: r["amount"], reverse=True)
    else:
        rows.sort(key=lambda r: (r["date"] is not None, r["date"]), reverse=True)
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": [
            {
                "fecha": r["date"].isoformat() if r["date"] else "",
                "descripcion": r["description"],
                "categoria": r["category"],
                "persona": r["person"],
                "cuenta": r["account"],
                "importe": round(r["amount"], 2),
                "archivo": r["filename"],
            }
            for r in page
        ],
    }


@router.get("/api/exportar.csv")
def export_csv(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    filters: Filters = Depends(_filters),
):
    rows = fetch_rows(db, filters)
    rows.sort(key=lambda r: (r["date"] is not None, r["date"]), reverse=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["fecha", "descripcion", "categoria", "persona", "cuenta", "importe", "archivo"])
    for r in rows:
        writer.writerow(
            [
                r["date"].isoformat() if r["date"] else "",
                r["description"],
                r["category"],
                r["person"],
                r["account"],
                f"{r['amount']:.2f}",
                r["filename"],
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gastos.csv"'},
    )

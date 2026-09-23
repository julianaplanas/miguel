"""Dashboard y API de datos agregados."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import Filters, available_options, build_summary, fetch_rows, parse_date
from app.categorize import SOURCE_MANUAL, SUGGESTED, UNCATEGORIZED
from app.db import get_db
from app.deps import require_user, templates
from app.models import Transaction, UploadedFile
from app.rules import apply_rule, save_rule

router = APIRouter()


def _filters(
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    persona: list[str] | None = Query(None),
    categoria: list[str] | None = Query(None),
    archivo: list[int] | None = Query(None),
    moneda: str | None = Query(None),
) -> Filters:
    return Filters(
        date_from=parse_date(desde),
        date_to=parse_date(hasta),
        persons=[p for p in (persona or []) if p],
        categories=[c for c in (categoria or []) if c],
        file_ids=archivo or [],
        currency=(moneda or "").strip().upper() or None,
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
                "id": r["id"],
                "fecha": r["date"].isoformat() if r["date"] else "",
                "descripcion": r["description"],
                "categoria": r["category"],
                "persona": r["person"],
                "cuenta": r["account"],
                "importe": round(r["amount"], 2),
                "moneda": r["currency"],
                "archivo": r["filename"],
                "categoria_origen": r["category_source"],
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
    writer.writerow(["fecha", "descripcion", "categoria", "persona", "cuenta", "importe", "moneda", "archivo"])
    for r in rows:
        writer.writerow(
            [
                r["date"].isoformat() if r["date"] else "",
                r["description"],
                r["category"],
                r["person"],
                r["account"],
                f"{r['amount']:.2f}",
                r["currency"],
                r["filename"],
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gastos.csv"'},
    )


@router.patch("/api/transacciones/{tx_id}")
def update_transaction(
    tx_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    payload: dict = Body(...),
):
    """Cambia la categoria de un movimiento, opcionalmente creando una regla."""
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")

    categoria = (payload.get("categoria") or "").strip()
    if not categoria:
        raise HTTPException(status_code=400, detail="La categoria no puede estar vacia")

    categoria = categoria[:160]
    aplicados = 0
    regla = None

    # "Aplicar a todos los que digan lo mismo": se guarda como regla para
    # que tambien valga en los archivos que subas despues. Se aplica ANTES
    # de marcar este movimiento como manual, porque si no quedaria excluido
    # de su propia regla y el conteo diria uno menos.
    if payload.get("aplicar_a_similares"):
        patron = (payload.get("patron") or tx.description or "").strip()
        if patron:
            save_rule(db, patron, categoria, source="manual")
            aplicados = apply_rule(db, patron, categoria)
            regla = patron

    tx.category = categoria
    tx.category_source = SOURCE_MANUAL
    db.commit()

    return {
        "id": tx.id,
        "categoria": tx.category,
        "regla": regla,
        "aplicados": aplicados,
    }


@router.get("/api/categorias")
def api_categories(db: Session = Depends(get_db), _: str = Depends(require_user)):
    """Categorias ya usadas + las sugeridas, para los selectores."""
    usadas = {
        c for (c,) in db.execute(select(Transaction.category).distinct()).all()
        if c and c != UNCATEGORIZED
    }
    return {"categorias": sorted(usadas | set(SUGGESTED))}

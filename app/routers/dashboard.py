"""Dashboard y API de datos agregados."""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import Filters, available_options, build_summary, fetch_rows, parse_date
from app.categorize import SOURCE_MANUAL, SUGGESTED, UNCATEGORIZED, normalize
from app.db import get_db
from app.deps import require_user, templates
from app.models import Transaction, UploadedFile
from app.rules import apply_rule, refresh_counts, save_rule

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
    # Los archivos pendientes de revision no tienen movimientos todavia:
    # aparecerian como chips vacios que no hacen nada.
    files = (
        db.execute(
            select(UploadedFile)
            .where(UploadedFile.imported.is_(True))
            .order_by(UploadedFile.uploaded_at.desc())
        )
        .scalars()
        .all()
    )
    active = [f for f in files if f.is_active]
    pendientes = (
        db.execute(select(func.count(UploadedFile.id)).where(UploadedFile.imported.is_(False))).scalar()
        or 0
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "files": files,
            "active_count": len(active),
            "total_files": len(files),
            "pending_files": pendientes,
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


# Por que campo se puede ordenar la tabla de movimientos. El orden se hace
# en el servidor y no en el navegador porque la tabla viene paginada:
# ordenar solo lo que ya esta cargado daria un orden falso.
ORDENES = {
    "fecha": lambda r: (r["date"] is not None, r["date"] or dt.date.min),
    "descripcion": lambda r: (r["description"] or "").lower(),
    "categoria": lambda r: (r["category"] or "").lower(),
    "persona": lambda r: (r["person"] or "").lower(),
    "archivo": lambda r: (r["filename"] or "").lower(),
    "importe": lambda r: r["amount"],
    "moneda": lambda r: (r["currency"] or ""),
}


def _ordenar(rows: list[dict], orden: str, direccion: str) -> None:
    clave = ORDENES.get(orden, ORDENES["fecha"])
    rows.sort(key=clave, reverse=direccion != "asc")


@router.get("/api/transacciones")
def api_transactions(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    filters: Filters = Depends(_filters),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    orden: str = Query("fecha"),
    dir: str = Query("desc"),
):
    rows = fetch_rows(db, filters)
    _ordenar(rows, orden, dir)
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "orden": orden if orden in ORDENES else "fecha",
        "dir": "asc" if dir == "asc" else "desc",
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
    _ordenar(rows, "fecha", "desc")
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


@router.delete("/api/transacciones/{tx_id}")
def delete_transaction(
    tx_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    similares: bool = Query(False),
):
    """Borra un movimiento (o todos los que digan lo mismo).

    Existe porque ningun lector de PDF acierta siempre: si se cuela una
    linea de totales o una fila repetida, tiene que poder sacarse sin
    borrar el archivo entero y volver a subirlo. Con `similares` se van
    todos los que comparten descripcion, que es como suelen aparecer.
    """
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")

    descripcion = tx.description or ""
    objetivo = [tx]
    if similares and descripcion.strip():
        patron = normalize(descripcion)
        objetivo = [
            t
            for t in db.execute(select(Transaction)).scalars().all()
            if normalize(t.description or "") == patron
        ]

    archivos = {t.file_id for t in objetivo if t.file_id}
    for movimiento in objetivo:
        db.delete(movimiento)
    db.flush()
    # El contador del archivo se muestra en la pantalla de Archivos: si no
    # se actualiza, dice mas movimientos de los que quedan.
    refresh_counts(db, archivos)
    db.commit()
    return {"borrados": len(objetivo), "descripcion": descripcion}


@router.get("/api/categorias")
def api_categories(db: Session = Depends(get_db), _: str = Depends(require_user)):
    """Categorias ya usadas + las sugeridas, para los selectores."""
    usadas = {
        c for (c,) in db.execute(select(Transaction.category).distinct()).all()
        if c and c != UNCATEGORIZED
    }
    return {"categorias": sorted(usadas | set(SUGGESTED))}

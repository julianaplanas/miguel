"""Dashboard y API de datos agregados."""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import currency as cur
from app.analytics import (
    Filters,
    available_options,
    build_summary,
    fetch_rows,
    parse_date,
)
from app.ingest import parse_amount
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
                "editado": bool(r.get("edited")),
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


def _campos_editados(payload: dict, tx: Transaction) -> dict:
    """Valida los campos que llegan y devuelve solo los que cambian.

    Lo que no venga en el payload no se toca: asi el editor puede mandar un
    solo campo sin arrastrar el resto.
    """
    cambios: dict = {}

    if "categoria" in payload:
        categoria = (payload.get("categoria") or "").strip()[:160]
        if categoria:
            cambios["category"] = categoria

    if "descripcion" in payload:
        cambios["description"] = (payload.get("descripcion") or "").strip()[:512]

    if "persona" in payload:
        cambios["person"] = (payload.get("persona") or "").strip()[:160] or "Sin asignar"

    if "cuenta" in payload:
        cambios["account"] = (payload.get("cuenta") or "").strip()[:160]

    if "fecha" in payload:
        texto = (payload.get("fecha") or "").strip()
        if not texto:
            cambios["date"] = None
        else:
            fecha = parse_date(texto)
            if fecha is None:
                raise HTTPException(status_code=400, detail="Fecha no valida (usa aaaa-mm-dd)")
            cambios["date"] = fecha

    if "moneda" in payload:
        codigo = cur.normalize_code(payload.get("moneda"), "")
        if not codigo:
            raise HTTPException(status_code=400, detail="Moneda no reconocida")
        cambios["currency"] = codigo

    if "importe" in payload:
        importe = parse_amount(payload.get("importe"))
        if importe is None:
            raise HTTPException(status_code=400, detail="Importe no valido")
        cambios["amount"] = round(float(importe), 2)

    # Solo lo que de verdad cambia, para no marcar como editado lo que se
    # guardo igual que estaba.
    return {campo: valor for campo, valor in cambios.items() if getattr(tx, campo) != valor}


def _fila_api(db: Session, tx: Transaction) -> dict:
    """El movimiento con la misma forma que devuelve /api/transacciones."""
    archivo = db.get(UploadedFile, tx.file_id) if tx.file_id else None
    return {
        "id": tx.id,
        "fecha": tx.date.isoformat() if tx.date else "",
        "descripcion": tx.description or "",
        "categoria": tx.category or "",
        "persona": tx.person or "",
        "cuenta": tx.account or "",
        "importe": round(float(tx.amount or 0.0), 2),
        "moneda": (tx.currency or "").upper(),
        "archivo": archivo.filename if archivo else "",
        "categoria_origen": tx.category_source or "",
        "editado": bool(tx.edited),
    }


@router.patch("/api/transacciones/{tx_id}")
def update_transaction(
    tx_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    payload: dict = Body(...),
):
    """Corrige un movimiento: categoria, fecha, importe, moneda, persona...

    Ningun lector acierta siempre y el archivo original tampoco: una
    retencion en pesos puede venir en la columna de dolares. Lo que se
    corrige aca queda marcado como corregido a mano.
    """
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")

    campos = _campos_editados(payload, tx)
    categoria = campos.pop("category", None)
    if categoria is None and "categoria" in payload:
        raise HTTPException(status_code=400, detail="La categoria no puede estar vacia")

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

    if categoria is not None:
        tx.category = categoria
        tx.category_source = SOURCE_MANUAL
    for campo, valor in campos.items():
        setattr(tx, campo, valor)
    if campos:
        tx.edited = True
    db.commit()

    return {
        "id": tx.id,
        "categoria": tx.category,
        "regla": regla,
        "aplicados": aplicados,
        "fila": _fila_api(db, tx),
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

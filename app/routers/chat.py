"""Chat con el modelo de OpenRouter sobre los datos activos."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_user, templates
from app.llm import OpenRouterError, build_messages, complete
from app.models import ChatMessage, UploadedFile

router = APIRouter()

SUGGESTIONS = [
    "Resume mis gastos y dime las 3 cosas mas importantes",
    "Que categoria crecio mas en los ultimos meses?",
    "Compara el gasto por persona y hazme un grafico",
    "Detecta gastos atipicos o posibles suscripciones repetidas",
    "En que puedo ahorrar 100 al mes sin cambiar mucho?",
]


def _history(db: Session, limit: int = 40) -> list[ChatMessage]:
    rows = (
        db.execute(select(ChatMessage).order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return list(reversed(rows))


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_user)):
    settings = get_settings()
    active_files = db.execute(select(UploadedFile).where(UploadedFile.is_active.is_(True))).scalars().all()
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "messages": [
                {"role": m.role, "content": m.content, "created_at": m.created_at} for m in _history(db)
            ],
            "chat_enabled": settings.chat_enabled,
            "model": settings.openrouter_model,
            "active_files": active_files,
            "suggestions": SUGGESTIONS,
            "active_page": "chat",
        },
    )


@router.get("/api/chat/historial")
def api_history(db: Session = Depends(get_db), _: str = Depends(require_user)):
    return {
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in _history(db)
        ]
    }


@router.post("/api/chat")
async def api_chat(
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    payload: dict = Body(...),
):
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="El mensaje esta vacio.")
    model = (payload.get("model") or "").strip() or None

    history = [{"role": m.role, "content": m.content} for m in _history(db, limit=20)]
    messages = build_messages(db, history, message)

    db.add(ChatMessage(role="user", content=message))
    db.commit()

    try:
        answer = await complete(messages, model=model)
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    settings = get_settings()
    db.add(ChatMessage(role="assistant", content=answer, model=model or settings.openrouter_model))
    db.commit()
    return {"reply": answer, "model": model or settings.openrouter_model}


@router.post("/api/chat/limpiar")
def api_clear(db: Session = Depends(get_db), _: str = Depends(require_user)):
    db.execute(delete(ChatMessage))
    db.commit()
    return {"ok": True}

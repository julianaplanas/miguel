"""Punto de entrada de la aplicacion FastAPI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.deps import templates
from app.routers import auth, categories, chat, dashboard, files
from app.routers import settings as settings_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gastos")

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    if settings.secret_key == "dev-secret-cambiame":
        logger.warning("SECRET_KEY no configurada: usa una clave propia en produccion.")
    if not settings.chat_enabled:
        logger.warning("OPENROUTER_API_KEY no configurada: el chat estara deshabilitado.")
    yield


app = FastAPI(
    title=settings.app_name,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(files.router)
app.include_router(chat.router)
app.include_router(categories.router)
app.include_router(settings_router.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """401 en una pagina normal -> redirige al login; en la API -> JSON."""
    wants_json = request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "")
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and not wants_json:
        target = quote(str(request.url.path), safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=status.HTTP_303_SEE_OTHER)
    if wants_json:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )

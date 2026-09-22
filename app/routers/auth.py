"""Login y logout."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.deps import templates
from app.security import COOKIE_NAME, check_credentials, create_session_token, is_authenticated

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if is_authenticated(request):
        return RedirectResponse(next or "/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None, "next": next})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if not check_credentials(username, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Usuario o contrasena incorrectos.", "next": next},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    settings = get_settings()
    target = next if next.startswith("/") else "/"
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(username.strip()),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response

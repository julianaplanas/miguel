"""Autenticacion sencilla de usuario unico con cookie de sesion firmada."""
from __future__ import annotations

import hmac

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "gastos_session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="gastos-auth")


def check_credentials(username: str, password: str) -> bool:
    settings = get_settings()
    user_ok = hmac.compare_digest(username.strip(), settings.username)
    pass_ok = hmac.compare_digest(password, settings.password)
    return user_ok and pass_ok


def create_session_token(username: str) -> str:
    return _serializer().dumps({"u": username})


def read_session(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=get_settings().session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    username = data.get("u")
    if username != get_settings().username:
        return None
    return username


def is_authenticated(request: Request) -> bool:
    return read_session(request) is not None

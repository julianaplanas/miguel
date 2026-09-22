"""Configuracion de la aplicacion, leida de variables de entorno."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si"}


class Settings:
    def __init__(self) -> None:
        self.app_name: str = os.getenv("APP_NAME", "Gastos")
        self.username: str = os.getenv("APP_USERNAME", "admin")
        self.password: str = os.getenv("APP_PASSWORD", "admin")
        self.secret_key: str = os.getenv("SECRET_KEY", "dev-secret-cambiame")
        self.session_max_age: int = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))
        self.cookie_secure: bool = _bool(os.getenv("COOKIE_SECURE"), default=bool(os.getenv("RAILWAY_ENVIRONMENT")))

        self.data_dir: Path = Path(os.getenv("DATA_DIR", "./data")).resolve()
        self.upload_dir: Path = self.data_dir / "uploads"

        self.currency: str = os.getenv("CURRENCY", "EUR")
        self.locale: str = os.getenv("LOCALE", "es-ES")

        self.openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
        self.openrouter_model: str = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
        self.openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.openrouter_app_title: str = os.getenv("OPENROUTER_APP_TITLE", "Gastos Dashboard")
        self.openrouter_referer: str = os.getenv("OPENROUTER_REFERER", "https://railway.app")

        self.max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "25"))

    @property
    def database_url(self) -> str:
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{self.data_dir / 'app.db'}"
        # Railway entrega postgres://; SQLAlchemy 2 necesita un driver explicito.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def chat_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

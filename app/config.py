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

        # Donde viven los archivos subidos y (si no hay Postgres) la base.
        # Railway expone la ruta del volumen en RAILWAY_VOLUME_MOUNT_PATH; si
        # no se configuro DATA_DIR a mano se usa esa, porque el default
        # './data' vive dentro del contenedor y se borra en cada deploy.
        self.volume_mount: str = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
        self.on_railway: bool = bool(
            os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")
        )
        configurado = os.getenv("DATA_DIR", "").strip()
        if configurado:
            self.data_dir_source = "DATA_DIR"
            elegido = configurado
        elif self.volume_mount:
            self.data_dir_source = "RAILWAY_VOLUME_MOUNT_PATH"
            elegido = self.volume_mount
        else:
            self.data_dir_source = "por defecto"
            elegido = "./data"
        self.data_dir: Path = Path(elegido).resolve()
        self.upload_dir: Path = self.data_dir / "uploads"

        self.currency: str = os.getenv("CURRENCY", "ARS").upper()
        self.locale: str = os.getenv("LOCALE", "es-AR")

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

    @property
    def data_is_persistent(self) -> bool:
        """True si los datos sobreviven a un deploy.

        Fuera de Railway no hay nada que avisar: el disco es el de siempre.
        En Railway solo persiste lo que este dentro del volumen montado.
        """
        if not self.on_railway:
            return True
        if not self.volume_mount:
            return False
        try:
            montaje = Path(self.volume_mount).resolve()
        except OSError:
            return False
        return self.data_dir == montaje or montaje in self.data_dir.parents

    @property
    def storage_warning(self) -> str:
        """Explicacion de por que los datos no van a sobrevivir, o cadena vacia."""
        if self.data_is_persistent:
            return ""
        if not self.volume_mount:
            return (
                "Estas en Railway y no hay ningun volumen montado: los archivos subidos "
                "y la base se borran en cada deploy. Crea un volumen y volve a desplegar."
            )
        return (
            f"Los datos se guardan en {self.data_dir}, que esta fuera del volumen "
            f"({self.volume_mount}): se borran en cada deploy. Quita DATA_DIR para que "
            f"se use el volumen, o apuntala a {self.volume_mount}."
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

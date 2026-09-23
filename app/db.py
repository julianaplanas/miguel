"""Motor y sesiones de base de datos."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()
_url = settings.database_url
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columnas anadidas despues de la primera version. create_all() solo crea
# tablas nuevas, no columnas nuevas, asi que estas se agregan a mano.
# (tabla, columna) -> (DDL, valor con el que rellenar las filas existentes).
# La columna se agrega como NULL y se rellena, de modo que una base migrada
# queda igual que una creada desde cero.
_ADDED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    "exchange_rates": {"source": ("VARCHAR(64)", "manual")},
}


def _ensure_columns(target=None) -> list[str]:
    """Agrega las columnas que falten. Devuelve las que ha creado."""
    target = target or engine
    inspector = inspect(target)
    creadas: list[str] = []
    for table, columns in _ADDED_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existentes = {c["name"] for c in inspector.get_columns(table)}
        for name, (ddl, relleno) in columns.items():
            if name in existentes:
                continue
            with target.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                conn.execute(
                    text(f"UPDATE {table} SET {name} = :valor WHERE {name} IS NULL"),
                    {"valor": relleno},
                )
            creadas.append(f"{table}.{name}")
    return creadas


def init_db() -> None:
    from app import models  # noqa: F401  (registra los modelos)

    models.Base.metadata.create_all(bind=engine)
    _ensure_columns()

"""Entorno aislado para los tests (usuario de prueba y base de datos temporal)."""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="gastos-test-")
os.environ.setdefault("APP_USERNAME", "tester")
os.environ.setdefault("APP_PASSWORD", "secreto123")
os.environ.setdefault("SECRET_KEY", "clave-de-test")
os.environ["DATA_DIR"] = _TMP
os.environ.pop("DATABASE_URL", None)
os.environ.pop("OPENROUTER_API_KEY", None)


def reset_db() -> None:
    """Deja la base como recien creada.

    Los modulos de test comparten la misma SQLite, asi que uno que cambie
    una preferencia (la moneda base, el modo de conversion) alteraria los
    resultados del siguiente. Cada modulo parte de cero.
    """
    from sqlalchemy import delete

    from app.db import SessionLocal, init_db
    from app.models import (
        AppSetting,
        CategoryRule,
        ChatMessage,
        ExchangeRate,
        RateHistory,
        Transaction,
        UploadedFile,
    )

    init_db()
    with SessionLocal() as db:
        for tabla in (
            Transaction,
            UploadedFile,
            ExchangeRate,
            RateHistory,
            ChatMessage,
            CategoryRule,
            AppSetting,
        ):
            db.execute(delete(tabla))
        db.commit()

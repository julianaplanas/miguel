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

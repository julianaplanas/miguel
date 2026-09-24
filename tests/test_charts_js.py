"""El tooltip de los graficos muestra el importe, no el indice de la fila."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "charts_harness.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_point_value_por_tipo_de_grafico():
    resultado = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert resultado.returncode == 0, resultado.stderr or resultado.stdout

"""Donde se guardan los datos y si eso sobrevive a un deploy.

El caso que motiva esto: un volumen creado en Railway pero DATA_DIR sin
configurar, asi que todo se escribia dentro del contenedor y se borraba
en cada deploy.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings


def _settings(monkeypatch, **entorno):
    """Construye Settings con un entorno controlado.

    Sin recargar el modulo: Settings lee el entorno al construirse, y un
    reload dejaria al resto de la app apuntando a una clase distinta.
    """
    for clave in ("DATA_DIR", "RAILWAY_VOLUME_MOUNT_PATH", "RAILWAY_ENVIRONMENT",
                  "RAILWAY_PROJECT_ID", "DATABASE_URL"):
        monkeypatch.delenv(clave, raising=False)
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    return Settings()


def test_sin_railway_el_default_es_local(monkeypatch):
    s = _settings(monkeypatch)
    assert s.data_dir == Path("./data").resolve()
    assert s.data_dir_source == "por defecto"
    # Fuera de Railway el disco es el de siempre: nada que avisar.
    assert s.data_is_persistent is True
    assert s.storage_warning == ""


def test_en_railway_con_volumen_se_usa_el_volumen(monkeypatch, tmp_path):
    volumen = str(tmp_path / "data")
    s = _settings(monkeypatch, RAILWAY_ENVIRONMENT="production",
                  RAILWAY_VOLUME_MOUNT_PATH=volumen)
    assert s.data_dir == Path(volumen).resolve()
    assert s.data_dir_source == "RAILWAY_VOLUME_MOUNT_PATH"
    assert s.data_is_persistent is True
    assert s.storage_warning == ""


def test_en_railway_sin_volumen_avisa(monkeypatch):
    """Este es el escenario que hace desaparecer los archivos."""
    s = _settings(monkeypatch, RAILWAY_ENVIRONMENT="production")
    assert s.data_is_persistent is False
    assert "ningun volumen" in s.storage_warning


def test_data_dir_fuera_del_volumen_avisa(monkeypatch, tmp_path):
    """Volumen montado, pero DATA_DIR apuntando a otro lado."""
    volumen = str(tmp_path / "volumen")
    s = _settings(monkeypatch, RAILWAY_ENVIRONMENT="production",
                  RAILWAY_VOLUME_MOUNT_PATH=volumen,
                  DATA_DIR=str(tmp_path / "otra"))
    assert s.data_is_persistent is False
    assert volumen in s.storage_warning


def test_data_dir_dentro_del_volumen_esta_bien(monkeypatch, tmp_path):
    volumen = tmp_path / "volumen"
    s = _settings(monkeypatch, RAILWAY_ENVIRONMENT="production",
                  RAILWAY_VOLUME_MOUNT_PATH=str(volumen),
                  DATA_DIR=str(volumen / "gastos"))
    assert s.data_is_persistent is True
    assert s.storage_warning == ""


def test_data_dir_explicito_le_gana_al_volumen(monkeypatch, tmp_path):
    """Si alguien configuro DATA_DIR a mano, manda esa."""
    volumen = tmp_path / "volumen"
    elegida = volumen / "sub"
    s = _settings(monkeypatch, RAILWAY_ENVIRONMENT="production",
                  RAILWAY_VOLUME_MOUNT_PATH=str(volumen),
                  DATA_DIR=str(elegida))
    assert s.data_dir == elegida.resolve()
    assert s.data_dir_source == "DATA_DIR"


def test_el_env_example_no_fija_una_ruta_relativa():
    """Regresion: '.env.example' traia DATA_DIR=./data.

    Copiado tal cual a Railway, eso manda los datos a /app/data, fuera del
    volumen, y se pierden en cada deploy. El ejemplo tiene que dejarla sin
    definir para que la app detecte el volumen.
    """
    lineas = Path(".env.example").read_text().splitlines()
    activas = [
        linea for linea in lineas
        if linea.strip().startswith("DATA_DIR=")
    ]
    assert activas == [], f"DATA_DIR no deberia venir definida: {activas}"

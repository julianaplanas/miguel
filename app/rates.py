"""Cotizaciones automaticas.

Dos proveedores, ambos publicos y sin clave:

- **dolarapi** (https://dolarapi.com) para cuando la base es ARS. Es el que
  importa en Argentina porque distingue oficial, blue, MEP, CCL y tarjeta:
  la diferencia entre ellos cambia el total por mucho, asi que la eleccion
  es del usuario y se guarda junto al tipo de cambio.
- **erapi** (https://open.er-api.com) como generico para cualquier otro par.

Si la API no responde se devuelve un error legible y el valor anterior se
queda como estaba: nunca se pisa un tipo cargado a mano con un fallo.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx

DOLARAPI = "https://dolarapi.com/v1"
ERAPI = "https://open.er-api.com/v6/latest"
# Series historicas: argentinadatos publica la cotizacion diaria del dolar
# desde hace anos; frankfurter cubre las monedas fuertes.
ARGENTINADATOS = "https://api.argentinadatos.com/v1/cotizaciones"
FRANKFURTER = "https://api.frankfurter.app"
TIMEOUT = 15.0
HISTORY_TIMEOUT = 60.0

# Casas de dolarapi.com, en el orden en que se ofrecen en la UI.
DOLAR_HOUSES: list[tuple[str, str]] = [
    ("blue", "Blue"),
    ("oficial", "Oficial"),
    ("bolsa", "MEP (bolsa)"),
    ("contadoconliqui", "Contado con liqui"),
    ("tarjeta", "Tarjeta"),
    ("mayorista", "Mayorista"),
    ("cripto", "Cripto"),
]
DEFAULT_HOUSE = "blue"

# Monedas que dolarapi publica contra el peso, ademas del dolar.
DOLARAPI_OTHERS = {"EUR": "eur", "BRL": "brl", "CLP": "clp", "UYU": "uyu"}

MANUAL = "manual"


class RateError(RuntimeError):
    """La cotizacion no se pudo obtener; el motivo es legible para el usuario."""


@dataclass
class FetchedRate:
    code: str
    base: str
    rate: float
    source: str
    detail: str = ""
    quoted_at: dt.datetime | None = None


def _get_json(url: str, timeout: float = TIMEOUT) -> Any:
    """Punto unico de salida a la red (los tests lo sustituyen)."""
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise RateError(f"La API respondio {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise RateError(f"No se pudo contactar con la API: {exc}") from exc
    except ValueError as exc:
        raise RateError("La API devolvio algo que no es JSON") from exc


def _parse_quoted_at(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def available_sources(code: str, base: str) -> list[tuple[str, str]]:
    """Origenes posibles para una moneda, como (valor, etiqueta)."""
    code, base = code.upper(), base.upper()
    opciones: list[tuple[str, str]] = [(MANUAL, "A mano")]
    if base == "ARS":
        if code == "USD":
            opciones += [(f"dolarapi:{casa}", f"dolarapi · {nombre}") for casa, nombre in DOLAR_HOUSES]
        elif code in DOLARAPI_OTHERS:
            opciones.append(("dolarapi", "dolarapi"))
    if code != base:
        opciones.append(("erapi", "open.er-api.com"))
    return opciones


def source_label(source: str) -> str:
    if not source or source == MANUAL:
        return "A mano"
    if source.startswith("dolarapi:"):
        casa = source.split(":", 1)[1]
        nombre = dict(DOLAR_HOUSES).get(casa, casa)
        return f"dolarapi · {nombre}"
    if source == "dolarapi":
        return "dolarapi"
    if source == "erapi":
        return "open.er-api.com"
    return source


def _fetch_dolarapi(code: str, base: str, house: str) -> FetchedRate:
    if base != "ARS":
        raise RateError("dolarapi solo cotiza contra pesos argentinos (ARS).")

    if code == "USD":
        casa = house or DEFAULT_HOUSE
        datos = _get_json(f"{DOLARAPI}/dolares/{casa}")
        fuente = f"dolarapi:{casa}"
        nombre = datos.get("nombre") or casa
    elif code in DOLARAPI_OTHERS:
        datos = _get_json(f"{DOLARAPI}/cotizaciones/{DOLARAPI_OTHERS[code]}")
        fuente = "dolarapi"
        nombre = datos.get("nombre") or code
    else:
        raise RateError(f"dolarapi no cotiza {code}.")

    if not isinstance(datos, dict):
        raise RateError("Respuesta inesperada de dolarapi.")

    # Se usa la venta: es lo que cuesta comprar esa moneda. Si no viene,
    # caemos al promedio con la compra y por ultimo a la compra sola.
    venta = _number(datos.get("venta"))
    compra = _number(datos.get("compra"))
    if venta and compra:
        valor = venta
        detalle = f"{nombre} · compra {compra:g} / venta {venta:g}"
    elif venta or compra:
        valor = venta or compra
        detalle = str(nombre)
    else:
        raise RateError("dolarapi no devolvio un precio valido.")

    return FetchedRate(
        code=code,
        base=base,
        rate=valor,
        source=fuente,
        detail=detalle,
        quoted_at=_parse_quoted_at(datos.get("fechaActualizacion")),
    )


def _fetch_erapi(code: str, base: str) -> FetchedRate:
    datos = _get_json(f"{ERAPI}/{code}")
    if not isinstance(datos, dict) or datos.get("result") == "error":
        raise RateError("open.er-api.com no reconocio la moneda.")
    tabla = datos.get("rates") or {}
    valor = _number(tabla.get(base))
    if valor is None:
        raise RateError(f"open.er-api.com no cotiza {code} contra {base}.")
    return FetchedRate(
        code=code,
        base=base,
        rate=valor,
        source="erapi",
        detail=f"1 {code} = {valor:g} {base}",
        quoted_at=_parse_quoted_at(datos.get("time_last_update_utc")),
    )


def fetch_rate(code: str, base: str, source: str = "") -> FetchedRate:
    """Busca la cotizacion de `code` en `base` con el origen pedido.

    Sin origen explicito usa dolarapi cuando la base es ARS y el generico
    en cualquier otro caso.
    """
    code, base = code.upper(), base.upper()
    if code == base:
        raise RateError("Es la propia moneda base.")

    if not source or source == MANUAL:
        source = f"dolarapi:{DEFAULT_HOUSE}" if base == "ARS" and code == "USD" else (
            "dolarapi" if base == "ARS" and code in DOLARAPI_OTHERS else "erapi"
        )

    if source.startswith("dolarapi"):
        casa = source.split(":", 1)[1] if ":" in source else ""
        return _fetch_dolarapi(code, base, casa)
    if source == "erapi":
        return _fetch_erapi(code, base)
    raise RateError(f"Origen desconocido: {source}")


# --------------------------------------------------------------------------
# Series historicas
# --------------------------------------------------------------------------

def _parse_date(value: Any) -> dt.date | None:
    if not value:
        return None
    texto = str(value).strip()[:10]
    try:
        return dt.date.fromisoformat(texto)
    except ValueError:
        return None


def history_source(code: str, base: str, source: str = "") -> str:
    """Que proveedor de historico corresponde a este par."""
    code, base = code.upper(), base.upper()
    if base == "ARS" and code == "USD":
        casa = source.split(":", 1)[1] if source.startswith("dolarapi:") else DEFAULT_HOUSE
        return f"argentinadatos:{casa}"
    return "frankfurter"


def history_label(source: str) -> str:
    if source.startswith("argentinadatos:"):
        casa = source.split(":", 1)[1]
        return f"argentinadatos · {dict(DOLAR_HOUSES).get(casa, casa)}"
    if source == "frankfurter":
        return "frankfurter.app"
    return source or "—"


def _history_argentinadatos(casa: str) -> list[tuple[dt.date, float]]:
    datos = _get_json(f"{ARGENTINADATOS}/dolares/{casa}", timeout=HISTORY_TIMEOUT)
    if not isinstance(datos, list):
        raise RateError("Respuesta inesperada de argentinadatos.")
    serie: list[tuple[dt.date, float]] = []
    for item in datos:
        if not isinstance(item, dict):
            continue
        fecha = _parse_date(item.get("fecha"))
        valor = _number(item.get("venta")) or _number(item.get("compra"))
        if fecha and valor:
            serie.append((fecha, valor))
    if not serie:
        raise RateError("argentinadatos no devolvio ninguna cotizacion.")
    return serie


def _history_frankfurter(code: str, base: str, desde: dt.date, hasta: dt.date) -> list[tuple[dt.date, float]]:
    url = f"{FRANKFURTER}/{desde.isoformat()}..{hasta.isoformat()}?from={code}&to={base}"
    datos = _get_json(url, timeout=HISTORY_TIMEOUT)
    if not isinstance(datos, dict) or "rates" not in datos:
        raise RateError(f"frankfurter.app no cotiza {code} contra {base}.")
    serie: list[tuple[dt.date, float]] = []
    for clave, valores in (datos.get("rates") or {}).items():
        fecha = _parse_date(clave)
        valor = _number((valores or {}).get(base))
        if fecha and valor:
            serie.append((fecha, valor))
    if not serie:
        raise RateError(f"frankfurter.app no devolvio datos para {code}/{base}.")
    return serie


def fetch_history(
    code: str,
    base: str,
    desde: dt.date,
    hasta: dt.date,
    source: str = "",
) -> tuple[list[tuple[dt.date, float]], str]:
    """Serie diaria de `code` en `base` para el periodo pedido.

    Devuelve (serie, origen). argentinadatos entrega la serie completa de
    una sola vez, asi que se recorta al periodo aqui.
    """
    code, base = code.upper(), base.upper()
    if code == base:
        raise RateError("Es la propia moneda base.")
    origen = history_source(code, base, source)

    if origen.startswith("argentinadatos:"):
        serie = _history_argentinadatos(origen.split(":", 1)[1])
        serie = [(f, v) for f, v in serie if desde <= f <= hasta]
        if not serie:
            raise RateError("No hay cotizaciones historicas en ese periodo.")
    else:
        serie = _history_frankfurter(code, base, desde, hasta)

    serie.sort(key=lambda par: par[0])
    return serie, origen

"""Deteccion y normalizacion de monedas.

Punto delicado para Argentina: `$` a secas significa pesos, mientras que
`US$`, `U$S` o `USD` significan dolares. Por eso `$` se resuelve como la
moneda por defecto del archivo (normalmente ARS) y los prefijos de dolar
se detectan antes, buscando siempre el alias mas largo primero.
"""
from __future__ import annotations

import re
import unicodedata

# Alias por codigo ISO. El simbolo `$` a secas NO aparece aqui a proposito.
ALIASES: dict[str, list[str]] = {
    "ARS": ["ars", "ar$", "$ar", "$a", "peso", "pesos", "peso argentino", "pesos argentinos"],
    "USD": [
        "usd", "us$", "u$s", "u$d", "usd$", "us dollar", "dolar", "dolares",
        "dollar", "dollars", "dolar billete", "dolar mep", "dolar blue",
    ],
    "EUR": ["eur", "€", "euro", "euros"],
    "BRL": ["brl", "r$", "real", "reales", "reais"],
    "UYU": ["uyu", "$u", "peso uruguayo", "pesos uruguayos"],
    "CLP": ["clp", "peso chileno", "pesos chilenos"],
    "MXN": ["mxn", "peso mexicano", "pesos mexicanos"],
    "GBP": ["gbp", "£", "libra", "libras", "pound", "pounds"],
}

# Nombre legible para la interfaz.
NAMES: dict[str, str] = {
    "ARS": "Peso argentino",
    "USD": "Dolar",
    "EUR": "Euro",
    "BRL": "Real",
    "UYU": "Peso uruguayo",
    "CLP": "Peso chileno",
    "MXN": "Peso mexicano",
    "GBP": "Libra",
}

_CODE_RE = re.compile(r"^[A-Z]{3}$")

# (alias, codigo) ordenados de alias mas largo a mas corto: 'u$s' gana a '$'.
_SORTED_ALIASES: list[tuple[str, str]] = sorted(
    ((alias, code) for code, aliases in ALIASES.items() for alias in aliases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def _normalize_text(value: object) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    # Conservamos los simbolos de moneda; solo quitamos tildes.
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_code(value: object, default: str = "") -> str:
    """Convierte 'u$s', 'dolares', 'USD'... en un codigo ISO ('USD').

    Devuelve `default` cuando el texto esta vacio o solo trae `$`, que es
    ambiguo y se resuelve con la moneda por defecto del archivo.
    """
    text = _normalize_text(value)
    if not text:
        return default

    upper = str(value).strip().upper()
    if _CODE_RE.match(upper) and upper in ALIASES:
        return upper

    for alias, code in _SORTED_ALIASES:
        if alias == text:
            return code
    for alias, code in _SORTED_ALIASES:
        if alias in text:
            return code

    # Codigo de tres letras que no conocemos: lo aceptamos tal cual.
    if _CODE_RE.match(upper):
        return upper
    return default


def detect_in_amount(value: object, default: str = "") -> str:
    """Busca la moneda dentro del propio importe: 'US$ 1.200,50' -> 'USD'.

    Si solo aparece `$` (o ningun simbolo) devuelve `default`, porque en
    Argentina `$` es pesos y en otro contexto seria otra cosa.
    """
    text = _normalize_text(value)
    if not text:
        return default
    # Nos quedamos con lo que no son digitos ni separadores.
    resto = re.sub(r"[\d\s.,()+-]", "", text)
    if not resto:
        return default
    return normalize_code(resto, default)


def label(code: str) -> str:
    """'USD' -> 'USD · Dolar' para los selectores."""
    code = (code or "").upper()
    nombre = NAMES.get(code)
    return f"{code} · {nombre}" if nombre else code


def known_codes() -> list[str]:
    return sorted(ALIASES.keys())

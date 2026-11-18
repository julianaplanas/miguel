"""De que banco y tarjeta es un archivo.

El nombre del banco esta en la cabecera del PDF ("Banco Galicia", "Santander
Rio", "Resumen de cuenta VISA") y a veces en el nombre del archivo. Se
detecta una vez al subir, queda guardado en el archivo y se puede corregir
a mano en la pantalla de revision. Es lo que permite preguntar "cuanto
gaste con la Visa del Galicia" sin que el modelo tenga que adivinarlo.

Se busca en la PRIMERA pagina, que es donde va la cabecera; mas adelante
aparecen "pago a Banco X" o "compra en Mastercard" que son movimientos, no
el emisor.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Patron -> nombre. El orden importa: los mas especificos primero.
BANCOS: list[tuple[str, str]] = [
    (r"\bsantander\b", "Santander"),
    (r"\bgalicia\b", "Galicia"),
    (r"\bbbva\b|banco frances", "BBVA"),
    (r"\bicbc\b", "ICBC"),
    (r"banco macro|\bmacro\b", "Macro"),
    (r"banco nacion|banco de la nacion|\bbna\b", "Banco Nacion"),
    (r"banco provincia|\bbapro\b", "Banco Provincia"),
    (r"banco ciudad", "Banco Ciudad"),
    (r"\bhsbc\b", "HSBC"),
    (r"\bpatagonia\b", "Patagonia"),
    (r"\bsupervielle\b", "Supervielle"),
    (r"\bcredicoop\b", "Credicoop"),
    (r"\bhipotecario\b", "Hipotecario"),
    (r"\bcomafi\b", "Comafi"),
    (r"\bitau\b", "Itau"),
    (r"\bbrubank\b", "Brubank"),
    (r"\buala\b", "Uala"),
    (r"mercado ?pago", "Mercado Pago"),
    (r"\bnaranja ?x?\b", "Naranja X"),
    (r"\bcaixabank\b|la caixa", "CaixaBank"),
    (r"\bsabadell\b", "Sabadell"),
    (r"\bbankinter\b", "Bankinter"),
    (r"\bing\b", "ING"),
    (r"\bopenbank\b", "Openbank"),
    (r"\brevolut\b", "Revolut"),
    (r"\bn26\b", "N26"),
    (r"\bwise\b", "Wise"),
]

TARJETAS: list[tuple[str, str]] = [
    (r"american ?express|\bamex\b", "American Express"),
    (r"\bmastercard\b|\bmaster card\b", "Mastercard"),
    (r"\bvisa\b", "Visa"),
    (r"\bcabal\b", "Cabal"),
    (r"\bnaranja\b", "Naranja"),
]

def _norm(texto: str) -> str:
    valor = unicodedata.normalize("NFKD", str(texto or "").lower())
    valor = "".join(ch for ch in valor if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", valor)


def _primero(patrones: list[tuple[str, str]], texto: str) -> str:
    """El nombre cuyo patron aparece ANTES en el texto: la cabecera manda."""
    mejor: tuple[int, str] | None = None
    for patron, nombre in patrones:
        m = re.search(patron, texto)
        if m and (mejor is None or m.start() < mejor[0]):
            mejor = (m.start(), nombre)
    return mejor[1] if mejor else ""


def detectar(texto_cabecera: str, filename: str = "") -> dict[str, str]:
    """Banco y tarjeta a partir de la cabecera del documento y del nombre.

    Devuelve {"banco": ..., "tarjeta": ...}, vacios si no se reconoce. La
    cabecera tiene prioridad sobre el nombre del archivo: el nombre lo pone
    el usuario y puede ser "resumen (3).pdf".
    """
    cabecera = _norm(texto_cabecera)[:3000]
    nombre = _norm(filename).replace("_", " ").replace("-", " ")
    banco = _primero(BANCOS, cabecera) or _primero(BANCOS, nombre)
    tarjeta = _primero(TARJETAS, cabecera) or _primero(TARJETAS, nombre)
    return {"banco": banco, "tarjeta": tarjeta}


def etiqueta(banco: str, tarjeta: str) -> str:
    """'Galicia Visa', 'Santander', 'Visa' o '' — como se muestra el origen."""
    return " ".join(p for p in (banco, tarjeta) if p)


def cabecera_pdf(data: bytes) -> str:
    """Texto de la primera pagina del PDF, o '' si no se puede leer."""
    import io

    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not pdf.pages:
                return ""
            return pdf.pages[0].extract_text() or ""
    except Exception:  # noqa: BLE001 - sin cabecera se sigue sin origen
        return ""


def cabecera_tabla(data: bytes) -> str:
    """Primeras lineas de un CSV/TSV: a veces el homebanking pone el banco."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return "\n".join(data.decode(encoding).splitlines()[:5])
        except UnicodeDecodeError:
            continue
    return ""


def detectar_archivo(data: bytes, filename: str) -> dict[str, Any]:
    nombre = str(filename or "")
    if nombre.lower().endswith(".pdf"):
        texto = cabecera_pdf(data)
    elif nombre.lower().endswith((".csv", ".tsv", ".txt")):
        texto = cabecera_tabla(data)
    else:
        texto = ""
    return detectar(texto, nombre)

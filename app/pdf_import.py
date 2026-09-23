"""Lectura de resumenes bancarios y de tarjeta en PDF.

Un PDF no tiene columnas de verdad, asi que esto es necesariamente
heuristico. Se intenta en dos pasadas:

1. **Tablas**: si el PDF trae tablas de verdad (las de homebanking suelen),
   se usa la que tenga una cabecera reconocible.
2. **Lineas**: si no, se leen las lineas de texto buscando el patron tipico
   de un resumen argentino: una fecha al principio, una descripcion, y uno
   o dos importes al final (movimiento y saldo).

Lo que sale es un DataFrame con columnas con nombre, asi que sigue el mismo
camino que un CSV: se detecta el mapeo y se puede corregir a mano desde la
pantalla de Archivos.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

# Importe: acepta 1.234,56 (formato argentino) y 1,234.56 (ingles), con
# simbolo delante y el signo menos detras, como lo imprimen varios bancos.
AMOUNT_RE = re.compile(
    r"(?P<pre>US\$|U\$S|u\$s|\$)?\s*"
    r"(?P<num>-?\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|-?\d+[.,]\d{2})"
    r"(?P<post>\s*-)?"
)
# Fecha al principio de la linea: 15/01, 15/01/26, 15-01-2026.
DATE_RE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
YEAR_RE = re.compile(r"\b(20\d{2})\b")

HEADER_DATE = {"fecha", "fecha operacion", "fecha de operacion", "dia", "date"}
HEADER_AMOUNT = {"importe", "monto", "debito", "credito", "amount", "valor", "cargo"}

COLUMNS = ["fecha", "descripcion", "importe", "moneda"]

# Lineas que llevan fecha e importe pero no son movimientos: arrastres de
# saldo y totales. Si entran, inflan el gasto con dinero que no se movio.
SKIP_WORDS = (
    "saldo anterior",
    "saldo inicial",
    "saldo final",
    "saldo actual",
    "saldo al",
    "total del periodo",
    "total periodo",
    "subtotal",
    "transporte",
)


def _is_movement(descripcion: str) -> bool:
    texto = _normalize(descripcion)
    return not any(palabra in texto for palabra in SKIP_WORDS)


class PdfImportError(ValueError):
    """El PDF no se pudo leer como lista de movimientos."""


def _normalize(text: Any) -> str:
    import unicodedata

    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value)


def _pages(data: bytes):
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                yield page
    except Exception as exc:  # noqa: BLE001 - pdfminer lanza de todo
        raise PdfImportError(f"No se pudo abrir el PDF: {exc}") from exc


def _document_year(texto: str) -> int | None:
    """Ano mas repetido del documento, para las fechas que vienen sin ano."""
    anos = YEAR_RE.findall(texto)
    if not anos:
        return None
    return int(Counter(anos).most_common(1)[0][0])


def _parse_date(match: re.Match, year_hint: int | None) -> str | None:
    dia, mes, ano = match.group(1), match.group(2), match.group(3)
    try:
        d, m = int(dia), int(mes)
    except ValueError:
        return None
    if not (1 <= d <= 31 and 1 <= m <= 12):
        return None
    if ano:
        a = int(ano)
        if a < 100:
            a += 2000
    elif year_hint:
        a = year_hint
    else:
        return None
    return f"{a:04d}-{m:02d}-{d:02d}"


def _amounts(line: str) -> list[tuple[str, str, int]]:
    """Importes de una linea, como (texto, moneda, posicion)."""
    encontrados = []
    for match in AMOUNT_RE.finditer(line):
        numero = match.group("num")
        if match.group("post"):
            numero = f"-{numero.lstrip('-')}"
        prefijo = (match.group("pre") or "").lower()
        moneda = "USD" if prefijo in {"us$", "u$s"} else ""
        encontrados.append((numero, moneda, match.start()))
    return encontrados


def _rows_from_lines(lineas: list[str], year_hint: int | None) -> list[dict[str, Any]]:
    candidatas: list[tuple[str, str, list[tuple[str, str, int]]]] = []
    for linea in lineas:
        fecha_match = DATE_RE.match(linea)
        if not fecha_match:
            continue
        fecha = _parse_date(fecha_match, year_hint)
        if not fecha:
            continue
        importes = _amounts(linea[fecha_match.end():])
        if not importes:
            continue
        candidatas.append((fecha, linea[fecha_match.end():], importes))

    if not candidatas:
        return []

    # Cuantos importes trae una linea tipica. Si son dos, el segundo suele
    # ser el saldo acumulado y hay que quedarse con el primero.
    tipico = Counter(len(imp) for _, _, imp in candidatas).most_common(1)[0][0]

    filas = []
    for fecha, resto, importes in candidatas:
        # Con dos importes por linea (movimiento + saldo), una linea con uno
        # solo suele ser el arrastre de saldo, no un movimiento.
        if tipico >= 2 and len(importes) < tipico:
            continue
        numero, moneda, posicion = importes[0]
        descripcion = re.sub(r"\s{2,}", " ", resto[:posicion].strip(" .-\t"))
        if not _is_movement(descripcion):
            continue
        filas.append(
            {
                "fecha": fecha,
                "descripcion": descripcion[:200],
                "importe": numero,
                "moneda": moneda,
            }
        )
    return filas


def _rows_from_tables(page: Any, year_hint: int | None) -> list[dict[str, Any]]:
    try:
        tablas = page.extract_tables()
    except Exception:  # noqa: BLE001
        return []

    filas: list[dict[str, Any]] = []
    for tabla in tablas or []:
        if not tabla or len(tabla) < 2:
            continue
        cabecera = [_normalize(c) for c in tabla[0]]
        col_fecha = next((i for i, c in enumerate(cabecera) if c in HEADER_DATE), None)
        col_importe = next(
            (i for i, c in enumerate(cabecera) if any(a in c for a in HEADER_AMOUNT)), None
        )
        if col_fecha is None or col_importe is None:
            continue
        col_desc = next(
            (
                i
                for i, c in enumerate(cabecera)
                if i not in {col_fecha, col_importe} and c
            ),
            None,
        )
        for cruda in tabla[1:]:
            if not cruda or len(cruda) <= max(col_fecha, col_importe):
                continue
            texto_fecha = str(cruda[col_fecha] or "").strip()
            match = DATE_RE.match(texto_fecha)
            if not match:
                continue
            fecha = _parse_date(match, year_hint)
            importes = _amounts(str(cruda[col_importe] or ""))
            if not fecha or not importes:
                continue
            numero, moneda, _ = importes[0]
            descripcion = str(cruda[col_desc] or "").strip() if col_desc is not None else ""
            if not _is_movement(descripcion):
                continue
            filas.append(
                {
                    "fecha": fecha,
                    "descripcion": descripcion[:200],
                    "importe": numero,
                    "moneda": moneda,
                }
            )
    return filas


def extract_rows(data: bytes) -> pd.DataFrame:
    """Devuelve los movimientos del PDF como DataFrame (fecha/descripcion/importe/moneda)."""
    paginas = list(_pages(data))
    if not paginas:
        raise PdfImportError("El PDF no tiene paginas.")

    textos = []
    for page in paginas:
        try:
            textos.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            textos.append("")
    texto_completo = "\n".join(textos)

    if not texto_completo.strip():
        raise PdfImportError(
            "El PDF no tiene texto: parece escaneado o es una imagen. "
            "Exporta el resumen en CSV/Excel desde el homebanking, o pasalo por un OCR."
        )

    year_hint = _document_year(texto_completo)

    filas: list[dict[str, Any]] = []
    for page in paginas:
        filas.extend(_rows_from_tables(page, year_hint))

    if not filas:
        filas = _rows_from_lines(texto_completo.splitlines(), year_hint)

    if not filas:
        raise PdfImportError(
            "No encontre movimientos en el PDF. Necesito lineas con una fecha "
            "al principio y un importe al final."
        )

    return pd.DataFrame(filas, columns=COLUMNS)


def extract_from_path(path: str | Path) -> pd.DataFrame:
    return extract_rows(Path(path).read_bytes())

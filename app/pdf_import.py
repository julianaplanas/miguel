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
# Un importe que ocupa una "palabra" entera del PDF. Se usa en la pasada por
# posicion: sirve para distinguir el importe del numero de comprobante y de
# los numeros que aparecen dentro de la descripcion.
WORD_AMOUNT_RE = re.compile(
    r"^(?:US\$|U\$S|u\$s|\$)?\s*"
    r"(-?\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|-?\d+[.,]\d{2})"
    r"(\s*-)?$"
)
# Fecha al principio de la linea: 15/01, 15/01/26, 15-01-2026 y tambien
# 29-Jul-26, que es como las imprimen los resumenes de tarjeta.
DATE_RE = re.compile(
    r"^\s*(\d{1,2})[/-](\d{1,2}|[A-Za-z\u00c0-\u017f]{3,10})(?:[/-](\d{2,4}))?\b"
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")

MONTHS = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5,
    "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9, "set": 9, "oct": 10,
    "nov": 11, "dic": 12, "dec": 12,
}

# Cabeceras que dicen en que moneda esta cada columna de importes. Al peso
# se le deja la moneda vacia a proposito: la elige el usuario al subir el
# archivo, asi el mismo lector sirve para un resumen uruguayo o chileno.
CURRENCY_COLUMNS = {
    "pesos": "",
    "peso": "",
    "importe": "",
    "dolares": "USD",
    "dolar": "USD",
    "usd": "USD",
    "u$s": "USD",
    "us$": "USD",
}

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
    # Resumenes de tarjeta: el pago del resumen anterior cancela consumos
    # que ya estan cargados, asi que contarlo restaria gastos reales.
    "su pago",
    "pago recibido",
    "pagos efectuados",
    "saldo pendiente",
    "total a pagar",
    "total consumos",
    "total del mes",
    "total compras",
    "total creditos",
    "total debitos",
    "total general",
    "importe total",
    "pago minimo",
    "limite de compra",
)


# Con estas palabras empieza una linea de totales o de saldos, no un gasto.
# Se comparan como palabra entera: "TOTALGAS SRL" es un comercio de verdad
# y "TOTAL A PAGAR" no.
SKIP_FIRST_WORDS = {
    "total",
    "totales",
    "subtotal",
    "saldo",
    "saldos",
    "suma",
    "sumas",
    "transporte",
    "consolidado",
}


def _is_movement(descripcion: str) -> bool:
    texto = _normalize(descripcion)
    if not texto:
        return False
    if any(palabra in texto for palabra in SKIP_WORDS):
        return False
    primera = texto.split(" ", 1)[0].strip(".:-")
    return primera not in SKIP_FIRST_WORDS


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


def _month_number(texto: str) -> int | None:
    """Numero de mes, venga como cifra (08) o abreviado (Ago, Aug)."""
    if texto.isdigit():
        return int(texto)
    return MONTHS.get(_normalize(texto)[:3])


def _parse_date(match: re.Match, year_hint: int | None) -> str | None:
    dia, mes, ano = match.group(1), match.group(2), match.group(3)
    mes_numero = _month_number(mes)
    if mes_numero is None:
        return None
    try:
        d, m = int(dia), mes_numero
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


def _page_lines(page: Any) -> list[list[dict[str, Any]]]:
    """Palabras de la pagina agrupadas en lineas, con sus coordenadas."""
    try:
        palabras = page.extract_words()
    except Exception:  # noqa: BLE001 - pdfminer lanza de todo
        return []
    palabras.sort(key=lambda w: (round(w["top"], 1), w["x0"]))

    lineas: list[list[dict[str, Any]]] = []
    actual: list[dict[str, Any]] = []
    tope: float | None = None
    for palabra in palabras:
        if tope is not None and abs(palabra["top"] - tope) <= 3:
            actual.append(palabra)
            continue
        if actual:
            lineas.append(actual)
        actual = [palabra]
        tope = palabra["top"]
    if actual:
        lineas.append(actual)
    return lineas


def _currency_columns(lineas: list[list[dict[str, Any]]]) -> list[tuple[float, float, str]]:
    """Columnas de importe segun su cabecera (PESOS / DOLARES).

    Se buscan en todo el documento, no pagina por pagina: la cabecera suele
    estar solo en la primera y las hojas siguientes siguen las mismas
    columnas. Buscandolas por pagina, las hojas sin cabecera se perdian
    enteras y el total quedaba corto sin decir nada.
    """
    columnas: list[tuple[float, float, str]] = []
    for linea in lineas:
        # Solo se miran las lineas de cabecera (las que encabezan la fecha).
        # Si no, un "US$" escrito delante de un importe pasaria por columna.
        if not any(_normalize(p["text"]) in HEADER_DATE for p in linea):
            continue
        for palabra in linea:
            codigo = CURRENCY_COLUMNS.get(_normalize(palabra["text"]))
            if codigo is None:
                continue
            columnas.append((palabra["x0"], palabra["x1"], codigo))
    return columnas


def _column_currency(palabra: dict[str, Any], columnas) -> str | None:
    """Moneda de la columna donde cae esa palabra, o None si no cae en ninguna."""
    mejor: str | None = None
    distancia: float | None = None
    for x0, x1, codigo in columnas:
        if palabra["x0"] <= x1 and palabra["x1"] >= x0:
            return codigo
        # Los importes van alineados a la derecha, asi que el borde derecho
        # es lo que mejor identifica la columna.
        actual = abs(palabra["x1"] - x1)
        if distancia is None or actual < distancia:
            mejor, distancia = codigo, actual
    if distancia is not None and distancia <= 20:
        return mejor
    return None


def _rows_from_layout(
    lineas: list[list[dict[str, Any]]],
    columnas: list[tuple[float, float, str]],
    year_hint: int | None,
) -> list[dict[str, Any]]:
    """Movimientos de un resumen con columnas de pesos y dolares.

    Se usa la posicion de cada palabra, no el orden del texto: en estos
    resumenes el importe de la linea puede estar en una columna o en la
    otra, y leyendo el texto plano no hay forma de saber cual. Ademas evita
    confundir el numero de comprobante, o un importe escrito dentro de la
    descripcion, con el importe del movimiento.
    """
    filas: list[dict[str, Any]] = []
    for linea in lineas:
        match = DATE_RE.match(linea[0]["text"])
        if not match:
            continue
        fecha = _parse_date(match, year_hint)
        if not fecha:
            continue

        elegido: tuple[str, str] | None = None
        descripcion: list[str] = []
        for palabra in linea[1:]:
            texto = palabra["text"]
            importe = WORD_AMOUNT_RE.match(texto)
            if importe:
                moneda = _column_currency(palabra, columnas)
                if moneda is not None:
                    if elegido is None:
                        numero = importe.group(1)
                        if importe.group(2):
                            numero = f"-{numero.lstrip('-')}"
                        elegido = (numero, moneda)
                    continue
            # El numero de comprobante cambia en cada linea: dejarlo en la
            # descripcion haria que ningun comercio se repitiera nunca, y
            # cada uno costaria una consulta al modelo.
            if texto.isdigit() and len(texto) >= 4:
                continue
            descripcion.append(texto)

        if elegido is None:
            continue
        texto = " ".join(descripcion).strip(" .-\t")
        if not texto or not _is_movement(texto):
            continue
        filas.append(
            {
                "fecha": fecha,
                "descripcion": texto[:200],
                "importe": elegido[0],
                "moneda": elegido[1],
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
        por_pagina = [_page_lines(page) for page in paginas]
        columnas: list[tuple[float, float, str]] = []
        for lineas in por_pagina:
            columnas.extend(_currency_columns(lineas))
        # Sin una columna de dolares no hace falta mirar posiciones: el
        # lector de texto plano de abajo alcanza y esta mas probado.
        if any(codigo == "USD" for _, _, codigo in columnas):
            for lineas in por_pagina:
                filas.extend(_rows_from_layout(lineas, columnas, year_hint))

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

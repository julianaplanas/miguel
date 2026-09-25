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

Reparto de trabajo, a proposito: aqui se hace lo que se puede verificar
(posiciones, fechas, importes al centavo) y lo estructural (una linea que
empieza por "total" no es un gasto). Decidir si "ARRASTRE EJERCICIO
ANTERIOR" es un movimiento es criterio, no parsing: eso lo decide el
modelo al importar y queda cacheado como regla. Los importes no pasan por
el modelo nunca: un error de parsing se ve, uno de un LLM no.
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
    "$": "",
    "ar$": "",
    "ars": "",
    "$ ars": "",
    "importe": "",
    "monto": "",
    "dolares": "USD",
    "dolar": "USD",
    "usd": "USD",
    "u$s": "USD",
    "us$": "USD",
    "u$d": "USD",
    "$ usd": "USD",
    "dolares usa": "USD",
}

HEADER_DATE = {"fecha", "fecha operacion", "fecha de operacion", "dia", "date"}
HEADER_AMOUNT = {"importe", "monto", "debito", "credito", "amount", "valor", "cargo"}

COLUMNS = ["fecha", "descripcion", "importe", "moneda"]

# Filtro minimo y ESTRUCTURAL: una linea que empieza por total, saldo o
# suma no es un gasto, la escriba como la escriba el banco. A proposito no
# se enumeran variantes ("total del mes", "sumatoria del periodo",
# "arrastre ejercicio anterior"): esa lista no tiene fondo y cada banco
# escribe lo suyo. De eso se encarga el modelo al importar, que decide una
# vez por descripcion y deja la respuesta cacheada como regla (app/rules.py,
# drop_non_movements). Esto es la red para cuando no hay modelo configurado.
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

# Lo mismo para lo que no encabeza la linea. El pago del resumen de tarjeta
# cancela consumos que ya estan listados: contarlo resta gasto real.
SKIP_WORDS = (
    "su pago",
    "pago recibido",
    "pagos efectuados",
    "pago minimo",
)


def _is_movement(descripcion: str) -> bool:
    texto = _normalize(descripcion)
    if not texto:
        return False
    if any(palabra in texto for palabra in SKIP_WORDS):
        return False
    primera = texto.split(" ", 1)[0].strip(".:-")
    return primera not in SKIP_FIRST_WORDS


def _anotar(
    skipped: list[dict[str, Any]] | None,
    fecha: str,
    descripcion: str,
    importes: list[tuple[str, str]],
    tipo: str = "total",
) -> None:
    """Deja constancia de una linea que se leyo pero no se importa.

    Lo que el lector descarta en silencio no se puede revisar. Los importes
    van con su moneda porque estas lineas suelen ser los totales que declara
    el propio documento: con ellos se puede comprobar, sumando, que lo
    importado cuadra.
    """
    if skipped is None:
        return
    # Una "linea" que es solo simbolos y numeros (la fila de limites, la de
    # cuotas a vencer) no es una linea que el lector se haya perdido: es
    # maquetacion. Se pide algo de texto para no llenar de ruido el panel.
    if tipo == "sin leer" and sum(c.isalpha() for c in descripcion) < 3:
        return
    skipped.append(
        {
            "tipo": tipo,
            "fecha": fecha,
            "descripcion": descripcion,
            "importes": [{"importe": numero, "moneda": moneda} for numero, moneda in importes],
        }
    )


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


def _rows_from_lines(
    lineas: list[str], year_hint: int | None, skipped: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
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
            _anotar(skipped, fecha, descripcion, [(numero, moneda)])
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


def _amount_words(lineas: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Todas las palabras de la pagina que son un importe."""
    return [p for linea in lineas for p in linea if WORD_AMOUNT_RE.match(p["text"])]


def _column_clusters(palabras: list[dict[str, Any]], tolerancia: float = 10.0) -> list[dict]:
    """Agrupa los importes por su borde derecho: cada grupo es una columna.

    Las columnas de una tabla salen de los DATOS, no de la cabecera: hay
    resumenes que la escriben "$ U$S", otros "PESOS DOLARES", otros no la
    repiten en las hojas siguientes. Los importes, en cambio, siempre estan
    alineados. Buscar la cabecera y descartar lo que no encaje hacia que un
    formato desconocido perdiera filas en silencio.
    """
    if not palabras:
        return []
    ordenadas = sorted(palabras, key=lambda p: p["x1"])
    grupos: list[list[dict[str, Any]]] = [[ordenadas[0]]]
    for palabra in ordenadas[1:]:
        if palabra["x1"] - grupos[-1][-1]["x1"] <= tolerancia:
            grupos[-1].append(palabra)
        else:
            grupos.append([palabra])

    columnas = []
    for grupo in grupos:
        # Una columna de verdad tiene varias filas. Un importe suelto en
        # medio de un parrafo no la hace.
        if len(grupo) < 3:
            continue
        columnas.append(
            {
                "x0": min(p["x0"] for p in grupo),
                "x1": max(p["x1"] for p in grupo),
                "codigo": "",
                "n": len(grupo),
            }
        )
    return sorted(columnas, key=lambda c: c["x1"])


def _label_columns(columnas: list[dict], lineas: list[list[dict[str, Any]]]) -> None:
    """Le pone moneda a cada columna, si alguna cabecera la nombra.

    La cabecera solo ETIQUETA columnas que ya existen: si no aparece, o no
    se entiende, la columna sigue valiendo y se usa la moneda del archivo.
    """
    for linea in lineas:
        for palabra in linea:
            codigo = CURRENCY_COLUMNS.get(_normalize(palabra["text"]))
            if not codigo:  # None (no es cabecera) o "" (moneda del archivo)
                continue
            for columna in columnas:
                if palabra["x0"] <= columna["x1"] and palabra["x1"] >= columna["x0"]:
                    columna["codigo"] = codigo


def _column_index(palabra: dict[str, Any], columnas: list[dict]) -> int | None:
    """En que columna cae ese importe, o None si en ninguna."""
    mejor: int | None = None
    distancia: float | None = None
    for indice, columna in enumerate(columnas):
        if palabra["x0"] <= columna["x1"] and palabra["x1"] >= columna["x0"]:
            return indice
        actual = abs(palabra["x1"] - columna["x1"])
        if distancia is None or actual < distancia:
            mejor, distancia = indice, actual
    if distancia is not None and distancia <= 12:
        return mejor
    return None


def _anotar_declarado(
    skipped: list[dict[str, Any]] | None,
    linea: list[dict[str, Any]],
    columnas: list[dict],
) -> None:
    """Guarda una linea sin fecha que lleva importes en las columnas."""
    if skipped is None:
        return
    palabras = []
    importes: list[tuple[str, str]] = []
    for palabra in linea:
        encontrado = WORD_AMOUNT_RE.match(palabra["text"])
        if encontrado:
            indice = _column_index(palabra, columnas)
            if indice is not None:
                numero = encontrado.group(1)
                if encontrado.group(2):
                    numero = f"-{numero.lstrip('-')}"
                importes.append((numero, columnas[indice]["codigo"]))
                continue
        palabras.append(palabra["text"])
    if not importes:
        return
    texto = " ".join(palabras).strip()
    # Un total de verdad (mismo criterio estructural que para las lineas con
    # fecha) o algo que cae en las columnas de importe y no se supo leer.
    # Lo segundo es lo que delata a un lector que se esta dejando filas.
    primera = _normalize(texto).split(" ", 1)[0].strip(".:-") if texto else ""
    tipo = "total" if primera in SKIP_FIRST_WORDS else "sin leer"
    _anotar(skipped, "", texto, importes, tipo)


def _line_date(linea: list[dict[str, Any]], year_hint: int | None) -> tuple[str | None, int]:
    """Fecha de la linea y desde que palabra empieza la descripcion.

    Se busca en las dos primeras palabras porque hay resumenes que ponen
    delante un numero de tarjeta o un asterisco.
    """
    for posicion, palabra in enumerate(linea[:2]):
        match = DATE_RE.match(palabra["text"])
        fecha = _parse_date(match, year_hint) if match else None
        if fecha:
            return fecha, posicion + 1
    return None, 1


# Simbolos que algunos resumenes escriben como palabra suelta delante del
# importe ("COMPRA X   U$S 42,30"). No son descripcion: son la moneda.
CURRENCY_SYMBOLS = {
    "$": "",
    "ar$": "",
    "$a": "",
    "u$s": "USD",
    "us$": "USD",
    "usd": "USD",
    "u$d": "USD",
}


def _line_amounts(
    linea: list[dict[str, Any]], desde: int, columnas: list[dict]
) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Importes de la linea con su columna y su simbolo, y la descripcion."""
    importes: list[tuple[int, str, str]] = []
    descripcion: list[str] = []
    simbolo = ""
    for palabra in linea[desde:]:
        texto = palabra["text"]
        encontrado = WORD_AMOUNT_RE.match(texto)
        if encontrado:
            indice = _column_index(palabra, columnas)
            if indice is not None:
                numero = encontrado.group(1)
                if encontrado.group(2):
                    numero = f"-{numero.lstrip('-')}"
                importes.append((indice, numero, simbolo))
                simbolo = ""
                continue
        marca = CURRENCY_SYMBOLS.get(_normalize(texto))
        if marca is not None:
            # Se guarda para el proximo importe y no ensucia la descripcion.
            simbolo = marca
            continue
        # El numero de comprobante cambia en cada linea: dejarlo en la
        # descripcion haria que ningun comercio se repitiera nunca, y cada
        # uno costaria una consulta al modelo.
        if texto.isdigit() and len(texto) >= 4:
            continue
        descripcion.append(texto)
    return importes, descripcion


def _rows_from_layout(
    por_pagina: list[list[list[dict[str, Any]]]],
    columnas: list[dict],
    year_hint: int | None,
    skipped: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Movimientos leidos por la POSICION de cada importe, no por el texto.

    En el texto plano de un PDF las columnas quedan pegadas: un importe en
    la columna de dolares y otro en la de pesos se leen igual, y un numero
    escrito dentro de la descripcion parece el importe de la fila. Mirando
    donde cae cada importe eso se resuelve.

    Hay dos formas de tabla y se distinguen contando: si la fila tipica
    tiene UN importe, cada columna es una moneda; si tiene DOS, la segunda
    es el saldo acumulado y hay que quedarse con la primera (y una fila con
    un solo importe es un arrastre de saldo, no un movimiento).
    """
    if not columnas:
        return []

    candidatas = []
    for lineas in por_pagina:
        for linea in lineas:
            fecha, desde = _line_date(linea, year_hint)
            if not fecha:
                _anotar_declarado(skipped, linea, columnas)
                continue
            importes, descripcion = _line_amounts(linea, desde, columnas)
            candidatas.append((fecha, descripcion, importes, linea, desde))

    con_importe = [c for c in candidatas if c[2]]
    if not con_importe:
        return []
    tipico = Counter(len(c[2]) for c in con_importe).most_common(1)[0][0]

    filas: list[dict[str, Any]] = []
    for fecha, descripcion, importes, linea, desde in candidatas:
        texto = " ".join(descripcion).strip(" .-\t")
        if not importes:
            # Tiene fecha pero ningun importe cayo en una columna: o no es
            # un movimiento, o el lector no supo leerlo. Queda anotada para
            # que se vea en la revision.
            sueltos = [
                (WORD_AMOUNT_RE.match(p["text"]).group(1), "")
                for p in linea[desde:]
                if WORD_AMOUNT_RE.match(p["text"])
            ]
            if sueltos:
                _anotar(skipped, fecha, texto, sueltos, "sin leer")
            continue

        indice, numero, simbolo = importes[0]
        if tipico >= 2 and len(importes) < tipico:
            # Con dos importes por fila (movimiento + saldo), una fila con
            # uno solo suele ser el arrastre del saldo anterior.
            _anotar(skipped, fecha, texto, [(numero, columnas[indice]["codigo"])])
            continue

        if not texto or not _is_movement(texto):
            _anotar(
                skipped,
                fecha,
                texto,
                [(n, columnas[i]["codigo"] or sim) for i, n, sim in importes],
            )
            continue

        filas.append(
            {
                "fecha": fecha,
                "descripcion": texto[:200],
                "importe": numero,
                # La columna manda; el simbolo de la linea es el respaldo
                # para los resumenes que no etiquetan las columnas.
                "moneda": columnas[indice]["codigo"] or simbolo,
            }
        )
    return filas


def _rows_from_tables(
    page: Any, year_hint: int | None, skipped: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
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
                _anotar(skipped, fecha, descripcion, [(numero, moneda)])
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


def extract_rows(data: bytes, skipped: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Devuelve los movimientos del PDF como DataFrame (fecha/descripcion/importe/moneda).

    En `skipped`, si se pasa, quedan las lineas que se leyeron pero no se
    importan (totales, saldos): sin eso no hay forma de revisarlas.
    """
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

    # Las tres lecturas se prueban y gana la que saca mas movimientos. Antes
    # se iba a la siguiente solo si la anterior no sacaba NADA, y con eso
    # una lectura que encontraba la mitad de las filas tapaba a la que las
    # encontraba todas: el resumen entraba incompleto y nada lo decia.
    por_tablas: list[dict[str, Any]] = []
    for page in paginas:
        por_tablas.extend(_rows_from_tables(page, year_hint))

    por_pagina = [_page_lines(page) for page in paginas]
    palabras = [p for lineas in por_pagina for p in _amount_words(lineas)]
    columnas = _column_clusters(palabras)
    for lineas in por_pagina:
        _label_columns(columnas, lineas)
    por_posicion = _rows_from_layout(por_pagina, columnas, year_hint)

    por_texto = _rows_from_lines(texto_completo.splitlines(), year_hint)

    # En empate gana la posicion: distingue la moneda de cada columna y no
    # confunde un numero de la descripcion con el importe de la fila.
    candidatas = [
        (len(por_posicion), 2, "posicion"),
        (len(por_tablas), 1, "tablas"),
        (len(por_texto), 0, "texto"),
    ]
    _, _, mejor = max(candidatas)
    filas = {"posicion": por_posicion, "tablas": por_tablas, "texto": por_texto}[mejor]

    if not filas:
        raise PdfImportError(
            "No encontre movimientos en el PDF. Necesito lineas con una fecha "
            "al principio y un importe al final."
        )

    # Las lineas descartadas se anotan con la lectura que gano, para que lo
    # que se muestra en la revision se corresponda con lo que se importa.
    if skipped is not None:
        if mejor == "posicion":
            _rows_from_layout(por_pagina, columnas, year_hint, skipped)
        elif mejor == "tablas":
            for page in paginas:
                _rows_from_tables(page, year_hint, skipped)
        else:
            _rows_from_lines(texto_completo.splitlines(), year_hint, skipped)

    return pd.DataFrame(filas, columns=COLUMNS)


def extract_from_path(path: str | Path) -> pd.DataFrame:
    return extract_rows(Path(path).read_bytes())

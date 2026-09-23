"""Lectura de archivos (CSV/Excel) y normalizacion a transacciones.

La idea es que puedas subir practicamente cualquier export de banco o
planilla de gastos: se detectan las columnas por nombre y se normalizan
fechas e importes. Si la deteccion falla, el mapeo se puede corregir a mano
desde la pantalla de Archivos y el archivo se reimporta.
"""
from __future__ import annotations

import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app import currency as cur
from app.pdf_import import extract_rows as extract_pdf_rows

FIELDS = ["date", "amount", "description", "category", "person", "account", "currency", "kind"]

ALIASES: dict[str, list[str]] = {
    "date": [
        "fecha", "fecha operacion", "fecha de operacion", "fecha valor", "fecha contable",
        "fecha movimiento", "fecha de la compra", "dia", "date", "transaction date",
        "posted date", "booking date", "fecha_operacion", "f. operacion", "fecha compra",
    ],
    "amount": [
        "importe", "monto", "cantidad", "amount", "valor", "total", "precio", "gasto",
        "importe eur", "importe (eur)", "debe", "cargo", "importe movimiento", "coste",
        "costo", "value", "importe total", "monto total", "spend",
    ],
    "description": [
        "descripcion", "concepto", "detalle", "description", "comercio", "merchant",
        "memo", "nota", "notas", "observaciones", "titulo", "item", "producto", "note",
        "establecimiento", "beneficiario", "referencia",
    ],
    "category": [
        "categoria", "category", "rubro", "tipo de gasto", "grupo", "clasificacion",
        "subcategoria", "etiqueta", "tag", "tipo gasto", "familia",
    ],
    "person": [
        "persona", "person", "quien", "usuario", "user", "miembro", "responsable",
        "pagador", "paid by", "payer", "nombre", "who", "integrante", "titular",
        "pagado por", "socio",
    ],
    "account": [
        "cuenta", "account", "tarjeta", "card", "metodo", "medio de pago",
        "metodo de pago", "payment method", "forma de pago", "banco", "wallet",
    ],
    "currency": ["moneda", "divisa", "currency", "curr"],
    "kind": ["tipo", "type", "movimiento", "operacion", "signo", "ingreso/gasto", "debito/credito"],
}

INCOME_WORDS = {"ingreso", "ingresos", "income", "abono", "credito", "haber", "entrada", "cobro", "salario", "sueldo"}
EXPENSE_WORDS = {"gasto", "gastos", "expense", "cargo", "debito", "debe", "salida", "pago", "compra", "retiro"}

_AMOUNT_CLEAN_RE = re.compile(r"[^0-9,.\-+()]")


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[_\-/\\]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def read_table(path: str | Path, raw: bytes | None = None) -> pd.DataFrame:
    """Lee un CSV/TSV/Excel en un DataFrame, tolerando encodings y separadores."""
    path = Path(path)
    suffix = path.suffix.lower()
    data = raw if raw is not None else path.read_bytes()

    if suffix == ".pdf":
        return extract_pdf_rows(data)

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(io.BytesIO(data), dtype=object)

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as exc:  # pragma: no cover - fallback
            last_error = exc
            continue
        for sep in (None, ",", ";", "\t", "|"):
            try:
                df = pd.read_csv(
                    io.StringIO(text),
                    sep=sep,
                    engine="python",
                    dtype=object,
                    skip_blank_lines=True,
                )
            except Exception as exc:  # noqa: BLE001 - probamos el siguiente separador
                last_error = exc
                continue
            if df.shape[1] >= 2 or sep == "|":
                return df
    if last_error:
        raise ValueError(f"No se pudo leer el archivo: {last_error}")
    raise ValueError("No se pudo leer el archivo")


def detect_mapping(columns: list[Any]) -> dict[str, str]:
    """Asocia cada campo canonico con la columna mas probable del archivo."""
    normalized = {str(col): normalize_header(col) for col in columns}
    mapping: dict[str, str] = {}
    taken: set[str] = set()

    # 1) Primero TODAS las coincidencias exactas: un encabezado que es
    #    exactamente 'tipo' pertenece a `kind`, aunque 'tipo de gasto' sea
    #    tambien un alias de `category`.
    for field_name in FIELDS:
        for col, norm in normalized.items():
            if col in taken:
                continue
            if norm in ALIASES[field_name]:
                mapping[field_name] = col
                taken.add(col)
                break

    # 2) Despues, coincidencias parciales: el encabezado contiene al alias
    #    ('fecha operacion' -> fecha, 'importe (eur)' -> importe).
    for field_name in FIELDS:
        if field_name in mapping:
            continue
        for alias in ALIASES[field_name]:
            found = None
            for col, norm in normalized.items():
                if col in taken or not norm:
                    continue
                if alias in norm:
                    found = col
                    break
            if found:
                mapping[field_name] = found
                taken.add(found)
                break
    return mapping


def parse_amount(value: Any) -> float | None:
    """Convierte '1.234,56 EUR', '(1,234.56)', '-12,5' ... a float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = _AMOUNT_CLEAN_RE.sub("", text).replace("(", "").replace(")", "")
    if not text or text in {"-", "+", ".", ","}:
        return None

    if "," in text and "." in text:
        # el separador decimal es el ultimo que aparece
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        decimals = len(text.split(",")[-1])
        text = text.replace(",", "." if decimals in (1, 2) else "")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    try:
        amount = float(text)
    except ValueError:
        return None
    return -amount if negative else amount


def parse_dates(series: pd.Series) -> pd.Series:
    """Parsea fechas probando formato dia/mes y mes/dia, quedandose con el mejor."""
    day_first = pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    month_first = pd.to_datetime(series, errors="coerce", dayfirst=False, format="mixed")
    if month_first.notna().sum() > day_first.notna().sum():
        return month_first
    return day_first


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return default
    return text[:500]


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, (int, str, bool)):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return str(value)


@dataclass
class ParsedFile:
    rows: list[dict[str, Any]] = field(default_factory=list)
    mapping: dict[str, Any] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _row_currency(row: Any, mapping: dict[str, Any], default_currency: str) -> str:
    """Moneda de una fila: columna de moneda > simbolo en el importe > defecto."""
    if "currency" in mapping:
        code = cur.normalize_code(row.get(mapping["currency"]), "")
        if code:
            return code
    code = cur.detect_in_amount(row.get(mapping["amount"]), "")
    return code or default_currency


def parse_file(
    path: str | Path,
    mapping: dict[str, Any] | None = None,
    default_person: str = "",
    default_currency: str = "",
    raw: bytes | None = None,
) -> ParsedFile:
    """Convierte un archivo en filas normalizadas listas para guardar.

    Convencion de signo: el importe guardado es positivo cuando es un gasto y
    negativo cuando es un ingreso, independientemente de como venga el archivo.

    La moneda de cada fila sale, por orden: de la columna de moneda, de un
    simbolo dentro del propio importe ('US$ 1.200'), o de `default_currency`.
    """
    df = read_table(path, raw=raw)
    df = df.dropna(how="all")
    columns = [str(c) for c in df.columns]
    df.columns = columns

    warnings: list[str] = []
    mapping = dict(mapping or {})
    detected = detect_mapping(columns)
    for key, value in detected.items():
        mapping.setdefault(key, value)
    # descarta columnas mapeadas que ya no existen
    for key in list(mapping.keys()):
        if key in FIELDS and mapping[key] not in columns:
            mapping.pop(key)

    if "amount" not in mapping:
        # ultimo recurso: la columna con mas valores numericos
        best_col, best_hits = None, 0
        for col in columns:
            hits = sum(1 for v in df[col].head(50) if parse_amount(v) is not None)
            if hits > best_hits:
                best_col, best_hits = col, hits
        if best_col and best_hits >= max(1, min(5, len(df))):
            mapping["amount"] = best_col
            warnings.append(f"No se encontro una columna de importe; se uso '{best_col}'.")
        else:
            raise ValueError(
                "No se pudo identificar la columna de importe. "
                "Revisa el archivo o ajusta el mapeo manualmente."
            )

    amounts = df[mapping["amount"]].map(parse_amount)
    valid = amounts.notna()
    if not valid.any():
        raise ValueError("La columna de importe no contiene numeros validos.")

    dates = parse_dates(df[mapping["date"]]) if "date" in mapping else pd.Series([pd.NaT] * len(df), index=df.index)
    if "date" not in mapping:
        warnings.append("No se detecto columna de fecha: la evolucion temporal quedara vacia.")
    elif dates.isna().all():
        warnings.append("No se pudieron interpretar las fechas de la columna seleccionada.")

    kinds = df[mapping["kind"]].map(lambda v: normalize_header(v)) if "kind" in mapping else None

    default_currency = cur.normalize_code(
        default_currency or mapping.get("default_currency") or "", ""
    )
    if default_currency:
        mapping["default_currency"] = default_currency

    invert = mapping.get("invert_sign")
    if invert is None:
        numeric = amounts[valid].astype(float)
        negatives = int((numeric < 0).sum())
        # Export bancario tipico: los gastos vienen en negativo -> invertimos.
        invert = negatives >= max(1, int(0.25 * len(numeric)))
        mapping["invert_sign"] = bool(invert)
    invert = bool(invert)

    rows: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        amount = amounts.loc[idx]
        if amount is None or (isinstance(amount, float) and math.isnan(amount)):
            continue
        amount = float(amount)
        if invert:
            amount = -amount
        if kinds is not None:
            kind = str(kinds.loc[idx] or "")
            if any(word in kind for word in INCOME_WORDS):
                amount = -abs(amount)
            elif any(word in kind for word in EXPENSE_WORDS):
                amount = abs(amount)

        date_value = dates.loc[idx] if len(dates) else pd.NaT
        rows.append(
            {
                "date": None if pd.isna(date_value) else pd.Timestamp(date_value).date(),
                "amount": round(amount, 2),
                "description": _clean_text(row.get(mapping["description"])) if "description" in mapping else "",
                "category": _clean_text(row.get(mapping["category"]), "Sin categoria")[:160] if "category" in mapping else "Sin categoria",
                "person": (
                    _clean_text(row.get(mapping["person"]), default_person or "Sin asignar")[:160]
                    if "person" in mapping
                    else (default_person or "Sin asignar")[:160]
                ),
                "account": _clean_text(row.get(mapping["account"]))[:160] if "account" in mapping else "",
                "currency": _row_currency(row, mapping, default_currency),
                "raw": json.dumps({str(k): _json_safe(v) for k, v in row.items()}, ensure_ascii=False)[:8000],
            }
        )

    if not rows:
        raise ValueError("El archivo no contiene filas con importes validos.")

    return ParsedFile(rows=rows, mapping=mapping, columns=columns, warnings=warnings)

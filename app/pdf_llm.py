"""Lectura de un PDF con el modelo, cuando la automatica no alcanza.

El lector deterministico (app/pdf_import.py) acierta con los formatos que
conoce y es gratis, instantaneo y comprobable. Pero cada banco maqueta
distinto y siempre hay uno nuevo: mantener heuristicas para todos no tiene
fondo. Aca el modelo hace la extraccion, que es la parte que necesita
interpretar un documento.

Lo que NO hace el modelo es decidir si la lectura esta bien. Eso lo dice la
aritmetica: el propio resumen declara sus totales, y la suma de lo extraido
tiene que coincidir con alguno. Un modelo verificando a otro modelo no
agrega nada; una resta si.

Al modelo se le manda el texto con la maquetacion conservada
(`extract_text(layout=True)`), que es lo que deja ver que importe esta bajo
que columna: sin eso, un consumo en dolares y uno en pesos se leen igual.
"""
from __future__ import annotations

import json
import re
from typing import Any

EXTRACT_PROMPT = """Sos un lector de resumenes bancarios y de tarjeta
argentinos. Te paso el texto de UNA hoja de un resumen, con la maquetacion
conservada: los espacios mantienen las columnas, asi que un importe debajo
de la columna "DOLARES" (o "U$S", o "USD") esta en dolares y uno bajo
"PESOS" (o "$") esta en pesos.

Devolve dos cosas:

1. `movimientos`: cada gasto o ingreso real de la hoja. Un movimiento tiene
   fecha, un comercio o concepto, y un importe.
2. `totales`: las lineas que NO son movimientos pero declaran un total o un
   saldo (TOTAL A PAGAR, TOTAL CONSUMOS, SUBTOTAL, SALDO ANTERIOR, los
   subtotales por titular). Sirven para comprobar que no falte nada.

Reglas, en orden de importancia:

- **Copia el importe EXACTAMENTE como esta impreso**, con sus puntos y
  comas: "1.523.928,55", no 1523928.55 ni 1.523.929. No sumes, no
  redondees, no conviertas monedas, no calcules nada. Si un importe esta
  entre parentesis o con el signo detras, copialo tal cual.
- No inventes filas. Si una linea no se entiende, dejala afuera: es mejor
  que falte a que aparezca algo que el documento no dice.
- No te saltees filas. Todas las lineas de consumo de la hoja van, aunque
  se repitan, aunque el comercio sea raro, aunque el importe sea chico.
- `moneda`: "USD" si el importe esta en la columna de dolares o lleva U$S,
  US$ o USD; "{moneda}" si esta en la columna de pesos o lleva $; vacio si
  no hay ninguna indicacion.
- `fecha`: en formato AAAA-MM-DD. Los resumenes usan dd/mm o dd-Mmm-aa. Si
  el año no esta en la linea, sacalo del resto de la hoja.
- Para las cuotas, la fecha es la que imprime la linea (la de la compra
  original), no la del resumen.
- NO son movimientos, y van en `totales` o se descartan: totales y
  subtotales, saldos (anterior, actual, pendiente), el pago del propio
  resumen ("SU PAGO", "PAGO RECIBIDO"), limites de compra, tasas, cuotas a
  vencer, y cualquier texto informativo o legal.
- Si la hoja no tiene movimientos (es la hoja de condiciones, por ejemplo),
  devolve las dos listas vacias.

Responde SOLO con este JSON, sin texto alrededor:
{{"movimientos": [{{"fecha": "2026-08-05", "descripcion": "MERPAGO*COTO",
"importe": "286.740,00", "moneda": "{moneda}"}}],
"totales": [{{"descripcion": "TOTAL A PAGAR", "importe": "1.548.988,57",
"moneda": "{moneda}"}}]}}
"""

# Una hoja sin al menos un par de importes no tiene movimientos: no vale la
# pena gastar una llamada en la hoja de condiciones generales.
AMOUNT_HINT = re.compile(r"\d[\d.]*,\d{2}|\d[\d,]*\.\d{2}")
MIN_AMOUNTS = 2
MAX_PAGES = 15


def page_texts(data: bytes) -> list[str]:
    """Texto de cada hoja conservando la maquetacion."""
    import io

    import pdfplumber

    textos: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_PAGES]:
            try:
                textos.append(page.extract_text(layout=True) or "")
            except Exception:  # noqa: BLE001 - pdfminer lanza de todo
                textos.append("")
    return textos


def worth_reading(texto: str) -> bool:
    return len(AMOUNT_HINT.findall(texto)) >= MIN_AMOUNTS


def _clean(valor: Any) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def _rows(datos: dict, clave: str, moneda_base: str) -> list[dict[str, str]]:
    salida = []
    for item in datos.get(clave) or []:
        if not isinstance(item, dict):
            continue
        importe = _clean(item.get("importe"))
        if not importe:
            continue
        salida.append(
            {
                "fecha": _clean(item.get("fecha"))[:10],
                "descripcion": _clean(item.get("descripcion"))[:200],
                "importe": importe,
                "moneda": _clean(item.get("moneda")).upper()[:8],
            }
        )
    return salida


async def extract_pages(
    data: bytes,
    moneda_base: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Lee el PDF hoja por hoja con el modelo.

    Devuelve {"movimientos": [...], "totales": [...], "paginas": n,
    "errores": [...]}. Una hoja que falla no se lleva puestas las demas:
    se anota el error y se sigue, porque media lectura con el aviso es mas
    util que ninguna.
    """
    from app.llm import OpenRouterError, complete

    textos = page_texts(data)
    movimientos: list[dict[str, str]] = []
    totales: list[dict[str, str]] = []
    errores: list[str] = []
    leidas = 0

    system = EXTRACT_PROMPT.replace("{moneda}", moneda_base or "ARS")
    for numero, texto in enumerate(textos, start=1):
        if not worth_reading(texto):
            continue
        leidas += 1
        try:
            respuesta = await complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Hoja {numero}:\n\n{texto}"},
                ],
                model=model,
                max_tokens=16000,
            )
        except OpenRouterError as exc:
            errores.append(f"hoja {numero}: {exc}")
            continue

        limpio = respuesta.strip()
        inicio, fin = limpio.find("{"), limpio.rfind("}")
        if inicio == -1 or fin == -1:
            errores.append(f"hoja {numero}: el modelo no devolvio JSON")
            continue
        try:
            datos = json.loads(limpio[inicio : fin + 1])
        except ValueError:
            errores.append(f"hoja {numero}: JSON invalido")
            continue
        if not isinstance(datos, dict):
            errores.append(f"hoja {numero}: el modelo no devolvio un objeto")
            continue

        movimientos.extend(_rows(datos, "movimientos", moneda_base))
        totales.extend(_rows(datos, "totales", moneda_base))

    return {
        "movimientos": movimientos,
        "totales": totales,
        "paginas": leidas,
        "errores": errores,
    }

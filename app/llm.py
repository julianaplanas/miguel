"""Cliente de OpenRouter y construccion del contexto de datos para el chat."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.analytics import Filters, build_summary, fetch_rows
from app.categorize import SUGGESTED
from app.config import get_settings
from app.preferences import base_currency

SYSTEM_PROMPT = """Eres un analista financiero personal que ayuda a entender gastos.

Trabajas con los datos de los archivos ACTIVOS que el usuario subio a su panel.
Recibes un resumen agregado en JSON (totales por categoria, por persona, por mes,
cruce persona x categoria, mayores gastos y una muestra de transacciones).

Reglas:
- Responde SIEMPRE en espanol, claro y concreto.
- La moneda base es {currency}. El bloque `monedas` del resumen dice que monedas
  hay y su tipo de cambio. Si hay mas de una, NUNCA sumes importes de monedas
  distintas sin convertirlos, y di explicitamente en que moneda esta cada cifra.
  Si falta el tipo de cambio de alguna, dilo en vez de estimarlo.
- Basa cada afirmacion en los datos recibidos. Si algo no esta en los datos, dilo
  en vez de inventarlo.
- Da cifras con dos decimales y porcentajes cuando ayuden a comparar.
- Se breve por defecto (3-8 lineas) y amplia solo si te lo piden.
- Convencion de signo: importe positivo = gasto, importe negativo = ingreso.

Cuando un grafico ayude a responder, incluye AL FINAL del mensaje uno o mas
bloques de codigo con el lenguaje `chart` y este JSON exacto:

```chart
{"type": "bar", "title": "Gasto por categoria", "labels": ["Comida", "Ocio"],
 "series": [{"label": "Gasto", "data": [120.5, 80.0]}]}
```

`type` puede ser bar, line, doughnut o pie. Usa solo numeros reales del resumen.
No inventes datos para rellenar un grafico y no pongas mas de 12 etiquetas.
"""


class OpenRouterError(RuntimeError):
    pass


def build_data_context(db: Session, max_sample: int = 40) -> dict[str, Any]:
    """Resumen compacto de los datos activos que se envia al modelo."""
    summary = build_summary(db, Filters(), top_n=15)
    rows = fetch_rows(db, Filters())
    rows.sort(key=lambda r: (r["date"] or dt.date.min), reverse=True)
    sample = [
        {
            "fecha": r["date"].isoformat() if r["date"] else None,
            "descripcion": r["description"][:80],
            "categoria": r["category"],
            "persona": r["person"],
            "importe": round(r["amount"], 2),
            "moneda": r["currency"],
        }
        for r in rows[:max_sample]
    ]
    return {
        "moneda_base": base_currency(db),
        "monedas": {
            "presentes": summary["currencies"],
            "convertido_a": summary["currency"] if summary["converted"] else None,
            "tipos_de_cambio": summary["rates"],
            "sin_tipo_de_cambio": summary["missing_rates"],
            "totales_por_moneda": summary["by_currency"],
        },
        "kpis": summary["kpis"],
        "por_categoria": summary["by_category"],
        "por_persona": summary["by_person"],
        "por_mes": summary["by_month"],
        "persona_x_categoria": summary["person_category"],
        "mayores_gastos": summary["top_transactions"],
        "muestra_transacciones_recientes": sample,
    }


def build_messages(
    db: Session,
    history: list[dict[str, str]],
    user_message: str,
    max_history: int = 12,
) -> list[dict[str, str]]:
    settings = get_settings()
    context = build_data_context(db)
    system = SYSTEM_PROMPT.replace("{currency}", base_currency(db))
    system += "\n\nRESUMEN DE DATOS ACTIVOS (JSON):\n" + json.dumps(
        context, ensure_ascii=False, default=str
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-max_history:])
    messages.append({"role": "user", "content": user_message})
    return messages


async def complete(
    messages: list[dict[str, str]],
    model: str | None = None,
    max_tokens: int = 4000,
) -> str:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise OpenRouterError(
            "Falta OPENROUTER_API_KEY. Anadela en las variables de entorno para activar el chat."
        )
    payload = {
        "model": model or settings.openrouter_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
    }
    url = f"{settings.openrouter_base_url}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise OpenRouterError(f"No se pudo contactar con OpenRouter: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:500]
        raise OpenRouterError(f"OpenRouter respondio {response.status_code}: {detail}")

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"Respuesta inesperada de OpenRouter: {json.dumps(data)[:400]}") from exc


async def list_models(limit: int = 60) -> list[dict[str, str]]:
    """Lista modelos disponibles en OpenRouter (para el selector del chat)."""
    settings = get_settings()
    url = f"{settings.openrouter_base_url}/models"
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"} if settings.openrouter_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    models = []
    for item in data.get("data", [])[: limit * 4]:
        model_id = item.get("id")
        if not model_id:
            continue
        models.append({"id": model_id, "name": item.get("name") or model_id})
    models.sort(key=lambda m: m["id"])
    return models[:limit] if limit else models


CATEGORIZE_PROMPT = """Sos un asistente que clasifica movimientos de cuentas
bancarias argentinas.

Te paso una linea por movimiento: la descripcion tal como la imprime el banco
(abreviada, en mayusculas, con codigos), y entre parentesis si es gasto o
ingreso y su importe tipico en {moneda}. Para cada descripcion, decidi la
categoria.

Categorias preferidas: {categorias}

Reglas:
- Usa una de las preferidas siempre que encaje, aunque no sea perfecta. Solo
  invent una nueva si ninguna sirve; que sea corta y no una variante de otra
  que ya existe (no agregues "Comida" si ya esta "Restaurantes").
- La descripcion suele decir COMO se movio la plata ademas de para que.
  Clasifica por el destino, no por el medio: "PAGO TRANSFERENCIA EDENOR" es
  Servicios y "COMPRA VISA DEBITO COTO" es Supermercado. Usa Transferencias o
  Pago de tarjeta solo cuando no haya ningun indicio de a que corresponde.
- El importe ayuda a desambiguar: un alquiler no son mil pesos, y un kiosco no
  son quinientos mil.
- Los movimientos marcados como ingreso casi nunca son un gasto: un sueldo,
  una devolucion o una transferencia recibida no van en una categoria de
  consumo.
- Si la descripcion no alcanza (un numero de operacion suelto, un codigo
  interno), devolve "Sin categoria". No adivines.
- Algunas lineas no son movimientos aunque vengan con fecha e importe: los
  totales y subtotales del resumen, los saldos (anterior, actual, pendiente),
  los arrastres de saldo, las cabeceras repetidas, los limites de compra y el
  pago del propio resumen de tarjeta, que cancela consumos ya listados. Para
  esas devolve exactamente "No es un movimiento": se descartan, porque
  contarlas duplica gasto que ya esta en las lineas de abajo. Ante la duda,
  categorizala normal: es peor borrar un gasto real que dejar una linea de mas.
- Responde SOLO con un objeto JSON {{"descripcion": "categoria", ...}}, con
  una clave por descripcion recibida y la descripcion EXACTA como clave, sin
  texto alrededor ni bloques de codigo.
"""

def _parse_json_object(texto: str) -> dict[str, str]:
    """Saca el objeto JSON de la respuesta, tolerando bloques de codigo."""
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = limpio.split("```")[1] if "```" in limpio[3:] else limpio[3:]
        limpio = limpio.split("\n", 1)[-1] if limpio.lower().startswith("json") else limpio
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio == -1 or fin == -1:
        raise OpenRouterError("El modelo no devolvio un JSON reconocible.")
    try:
        datos = json.loads(limpio[inicio : fin + 1])
    except ValueError as exc:
        raise OpenRouterError("El modelo devolvio un JSON invalido.") from exc
    if not isinstance(datos, dict):
        raise OpenRouterError("El modelo no devolvio un objeto JSON.")
    return {str(k): str(v) for k, v in datos.items()}


NOT_MOVEMENT_PROMPT = """Sos un asistente que limpia movimientos ya
importados de resumenes bancarios y de tarjeta argentinos.

Te paso una linea por descripcion distinta, con su importe tipico y cuantas
veces aparece. Tu unica tarea es decir cuales NO son movimientos reales de
dinero, sino ruido del resumen que se colo al leer el PDF:

- totales y subtotales (del mes, del periodo, de consumos, a pagar)
- saldos: anterior, actual, pendiente, arrastres de saldo
- cabeceras o pies de tabla repetidos
- limites de compra, pagos minimos, cotizaciones informativas
- el pago del propio resumen de tarjeta, que cancela consumos que ya estan
  listados linea por linea

Un gasto o un ingreso de verdad NUNCA va en la lista, por raro que sea su
nombre: un comercio desconocido, un codigo, un numero de operacion suelto o
una transferencia son movimientos. Que el importe sea grande no lo convierte
en un total. Ante la duda, dejalo fuera: es peor borrar un gasto real que
dejar una linea de mas.

Responde SOLO con un JSON {{"descartar": ["descripcion exacta", ...]}}, con
las descripciones EXACTAS como te llegaron. Si no hay ninguna, responde
{{"descartar": []}}.
"""


async def find_non_movements(
    descriptions: list[dict],
    model: str | None = None,
) -> list[str]:
    """Pregunta al modelo cuales de esas descripciones no son movimientos."""
    if not descriptions:
        return []

    lineas = []
    for item in descriptions[:200]:
        texto = str(item.get("descripcion", "")).strip()
        if not texto:
            continue
        importe = item.get("importe")
        veces = item.get("veces", 1)
        detalle = f"{veces}x" if veces else ""
        if importe is not None:
            detalle = f"{detalle}, {importe:,.2f}" if detalle else f"{importe:,.2f}"
        lineas.append(f"- {texto} ({detalle})" if detalle else f"- {texto}")
    if not lineas:
        return []

    respuesta = await complete(
        [
            {"role": "system", "content": NOT_MOVEMENT_PROMPT.format()},
            {"role": "user", "content": "\n".join(lineas)},
        ],
        model=model,
        max_tokens=8000,
    )
    limpio = respuesta.strip()
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio == -1 or fin == -1:
        raise OpenRouterError("El modelo no devolvio un JSON reconocible.")
    try:
        datos = json.loads(limpio[inicio : fin + 1])
    except ValueError as exc:
        raise OpenRouterError("El modelo devolvio un JSON invalido.") from exc
    descartar = datos.get("descartar") if isinstance(datos, dict) else None
    if not isinstance(descartar, list):
        raise OpenRouterError("El modelo no devolvio la lista 'descartar'.")
    return [str(d).strip() for d in descartar if str(d).strip()]


async def suggest_categories(
    descriptions: list[str] | list[dict],
    model: str | None = None,
) -> dict[str, str]:
    """Pide al modelo una categoria por descripcion.

    Acepta texto suelto o diccionarios con `descripcion`, `importe` y
    `es_gasto`: el importe y el signo ayudan a desambiguar.
    """
    if not descriptions:
        return {}

    settings = get_settings()
    system = CATEGORIZE_PROMPT.format(
        categorias=", ".join(SUGGESTED), moneda=settings.currency
    )

    lineas = []
    for item in descriptions[:200]:
        if isinstance(item, dict):
            texto = str(item.get("descripcion", "")).strip()
            if not texto:
                continue
            tipo = "gasto" if item.get("es_gasto", True) else "ingreso"
            importe = item.get("importe")
            if importe is None:
                lineas.append(f"- {texto} ({tipo})")
            else:
                lineas.append(f"- {texto} ({tipo}, {importe:,.2f})")
        else:
            lineas.append(f"- {item}")
    if not lineas:
        return {}

    respuesta = await complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(lineas)},
        ],
        model=model,
        # Una categoria por descripcion ocupa bastante: con el limite viejo
        # de 1500 la respuesta se cortaba y se perdia el lote entero.
        max_tokens=16000,
    )
    return _parse_json_object(respuesta)

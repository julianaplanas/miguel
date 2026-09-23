"""Cliente de OpenRouter y construccion del contexto de datos para el chat."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.analytics import Filters, build_summary, fetch_rows
from app.config import get_settings

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
        "moneda_base": get_settings().currency,
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
    system = SYSTEM_PROMPT.replace("{currency}", settings.currency)
    system += "\n\nRESUMEN DE DATOS ACTIVOS (JSON):\n" + json.dumps(
        context, ensure_ascii=False, default=str
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-max_history:])
    messages.append({"role": "user", "content": user_message})
    return messages


async def complete(messages: list[dict[str, str]], model: str | None = None) -> str:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise OpenRouterError(
            "Falta OPENROUTER_API_KEY. Anadela en las variables de entorno para activar el chat."
        )
    payload = {
        "model": model or settings.openrouter_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1500,
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

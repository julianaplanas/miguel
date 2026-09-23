"""Deduccion de la categoria a partir de la descripcion del movimiento.

Un extracto bancario no trae categoria: trae "COMPRA COTO DIGITAL" y poco
mas. Aqui se resuelve con reglas de texto: si la descripcion contiene tal
palabra, la categoria es tal. Es deliberadamente simple y explicable — el
usuario ve exactamente por que se asigno cada cosa y puede cambiarla.

Las reglas por defecto estan pensadas para Argentina (comercios, servicios
y bancos de uso corriente). El usuario puede agregar las suyas, que se
guardan en la base y ganan a las de fabrica cuando son mas especificas.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Origen de la categoria de un movimiento.
SOURCE_FILE = "file"      # venia en el archivo
SOURCE_RULE = "rule"      # la dedujo una regla
SOURCE_AI = "ai"          # la sugirio el modelo
SOURCE_MANUAL = "manual"  # la escribio el usuario
SOURCE_NONE = ""

UNCATEGORIZED = "Sin categoria"

# Categorias sugeridas en los selectores (el usuario puede escribir otras).
SUGGESTED = [
    "Supermercado",
    "Restaurantes",
    "Transporte",
    "Vivienda",
    "Servicios",
    "Salud",
    "Suscripciones",
    "Ocio",
    "Ropa",
    "Educacion",
    "Mascotas",
    "Impuestos",
    "Comisiones bancarias",
    "Efectivo",
    "Transferencias",
    "Pago de tarjeta",
    "Ingresos",
]

# (patron, categoria). El patron se busca como texto dentro de la
# descripcion ya normalizada (minusculas y sin tildes).
DEFAULT_RULES: list[tuple[str, str]] = [
    # --- Supermercado ---
    ("coto", "Supermercado"),
    ("carrefour", "Supermercado"),
    ("jumbo", "Supermercado"),
    ("disco", "Supermercado"),
    ("vea digital", "Supermercado"),
    ("changomas", "Supermercado"),
    ("walmart", "Supermercado"),
    ("la anonima", "Supermercado"),
    ("supermercado", "Supermercado"),
    ("maxiconsumo", "Supermercado"),
    ("verduleria", "Supermercado"),
    ("carniceria", "Supermercado"),
    ("panaderia", "Supermercado"),
    ("almacen", "Supermercado"),
    # --- Restaurantes y delivery ---
    ("pedidosya", "Restaurantes"),
    ("pedidos ya", "Restaurantes"),
    ("rappi", "Restaurantes"),
    ("mcdonald", "Restaurantes"),
    ("burger king", "Restaurantes"),
    ("starbucks", "Restaurantes"),
    ("havanna", "Restaurantes"),
    ("restaurant", "Restaurantes"),
    ("parrilla", "Restaurantes"),
    ("cafeteria", "Restaurantes"),
    ("pizzeria", "Restaurantes"),
    ("heladeria", "Restaurantes"),
    # --- Transporte ---
    ("ypf", "Transporte"),
    ("shell", "Transporte"),
    ("axion", "Transporte"),
    ("puma energia", "Transporte"),
    ("sube", "Transporte"),
    ("subte", "Transporte"),
    ("uber", "Transporte"),
    ("cabify", "Transporte"),
    ("didi", "Transporte"),
    ("peaje", "Transporte"),
    ("autopista", "Transporte"),
    ("ausa", "Transporte"),
    ("aubasa", "Transporte"),
    ("estacionamiento", "Transporte"),
    ("cochera", "Transporte"),
    ("aerolineas", "Transporte"),
    ("flybondi", "Transporte"),
    # --- Vivienda ---
    ("alquiler", "Vivienda"),
    ("expensas", "Vivienda"),
    ("inmobiliaria", "Vivienda"),
    ("ferreteria", "Vivienda"),
    ("easy ", "Vivienda"),
    ("sodimac", "Vivienda"),
    # --- Servicios ---
    ("edenor", "Servicios"),
    ("edesur", "Servicios"),
    ("metrogas", "Servicios"),
    ("camuzzi", "Servicios"),
    ("aysa", "Servicios"),
    ("personal", "Servicios"),
    ("movistar", "Servicios"),
    ("claro", "Servicios"),
    ("telecentro", "Servicios"),
    ("fibertel", "Servicios"),
    ("flow", "Servicios"),
    ("directv", "Servicios"),
    ("amazon web services", "Servicios"),
    ("aws", "Servicios"),
    ("digitalocean", "Servicios"),
    ("vercel", "Servicios"),
    ("hosting", "Servicios"),
    # --- Suscripciones ---
    ("netflix", "Suscripciones"),
    ("spotify", "Suscripciones"),
    ("disney", "Suscripciones"),
    ("hbo max", "Suscripciones"),
    ("youtube premium", "Suscripciones"),
    ("amazon prime", "Suscripciones"),
    ("apple.com/bill", "Suscripciones"),
    ("google storage", "Suscripciones"),
    ("google one", "Suscripciones"),
    ("icloud", "Suscripciones"),
    ("dropbox", "Suscripciones"),
    ("notion", "Suscripciones"),
    ("canva", "Suscripciones"),
    ("adobe", "Suscripciones"),
    ("openai", "Suscripciones"),
    ("chatgpt", "Suscripciones"),
    ("anthropic", "Suscripciones"),
    ("claude.ai", "Suscripciones"),
    ("github", "Suscripciones"),
    # --- Salud ---
    ("farmacity", "Salud"),
    ("farmacia", "Salud"),
    ("osde", "Salud"),
    ("swiss medical", "Salud"),
    ("galeno", "Salud"),
    ("medife", "Salud"),
    ("sanatorio", "Salud"),
    ("clinica", "Salud"),
    ("hospital", "Salud"),
    ("dentista", "Salud"),
    ("odontolog", "Salud"),
    ("laboratorio", "Salud"),
    ("optica", "Salud"),
    # --- Ocio ---
    ("cinemark", "Ocio"),
    ("hoyts", "Ocio"),
    ("showcase", "Ocio"),
    ("cine", "Ocio"),
    ("teatro", "Ocio"),
    ("ticketek", "Ocio"),
    ("passline", "Ocio"),
    ("megatlon", "Ocio"),
    ("sportclub", "Ocio"),
    ("gimnasio", "Ocio"),
    ("steam", "Ocio"),
    ("playstation", "Ocio"),
    # --- Ropa ---
    ("zara", "Ropa"),
    ("adidas", "Ropa"),
    ("nike", "Ropa"),
    ("dexter", "Ropa"),
    ("falabella", "Ropa"),
    ("indumentaria", "Ropa"),
    # --- Educacion ---
    ("colegio", "Educacion"),
    ("universidad", "Educacion"),
    ("udemy", "Educacion"),
    ("coursera", "Educacion"),
    ("libreria", "Educacion"),
    # --- Mascotas ---
    ("veterinaria", "Mascotas"),
    ("puppis", "Mascotas"),
    # --- Impuestos ---
    ("afip", "Impuestos"),
    ("arba", "Impuestos"),
    ("agip", "Impuestos"),
    ("monotributo", "Impuestos"),
    ("impuesto", "Impuestos"),
    ("ley 25413", "Impuestos"),
    ("percepcion", "Impuestos"),
    ("retencion", "Impuestos"),
    ("sellos", "Impuestos"),
    ("abl", "Impuestos"),
    ("rentas", "Impuestos"),
    # --- Banco ---
    ("comision", "Comisiones bancarias"),
    ("mantenimiento de cuenta", "Comisiones bancarias"),
    ("mantenimiento cuenta", "Comisiones bancarias"),
    ("seguro de vida", "Comisiones bancarias"),
    ("interes punitorio", "Comisiones bancarias"),
    ("gastos administrativos", "Comisiones bancarias"),
    ("cajero", "Efectivo"),
    ("extraccion", "Efectivo"),
    ("banelco", "Efectivo"),
    ("transferencia", "Transferencias"),
    ("pago tarjeta", "Pago de tarjeta"),
    ("pago de tarjeta", "Pago de tarjeta"),
    ("visa", "Pago de tarjeta"),
    ("mastercard", "Pago de tarjeta"),
    # --- Ingresos ---
    ("sueldo", "Ingresos"),
    ("haberes", "Ingresos"),
    ("nomina", "Ingresos"),
    ("remuneracion", "Ingresos"),
    ("acreditacion", "Ingresos"),
    ("aguinaldo", "Ingresos"),
]

# Patrones que ganan aunque otro mas largo tambien coincida. El caso que lo
# motiva: "TRANSFERENCIA RECIBIDA SUELDO" es un ingreso, no una
# transferencia, pero "transferencia" es una palabra mas larga que "sueldo".
HIGH_PRIORITY = {
    "sueldo",
    "haberes",
    "nomina",
    "remuneracion",
    "aguinaldo",
    "alquiler",
    "expensas",
}


def normalize(text: str) -> str:
    """Minusculas, sin tildes y con los espacios colapsados."""
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value)


@dataclass(frozen=True)
class Rule:
    pattern: str
    category: str
    source: str = SOURCE_RULE
    priority: int = 0

    @property
    def normalized(self) -> str:
        return normalize(self.pattern)


def default_rules() -> list[Rule]:
    return [
        Rule(
            pattern=p,
            category=c,
            source="default",
            priority=10 if p in HIGH_PRIORITY else 0,
        )
        for p, c in DEFAULT_RULES
    ]


def sort_rules(rules: list[Rule]) -> list[Rule]:
    """Orden de aplicacion: prioridad, luego longitud del patron.

    A igual prioridad gana el patron mas largo, que es el mas especifico:
    'hbo max' le gana a 'max', y una regla propia como 'coto digital' le
    gana a la de fabrica 'coto'.
    """
    return sorted(
        rules,
        key=lambda r: (r.priority, len(r.normalized), r.source != "default"),
        reverse=True,
    )


def match(description: str, rules: list[Rule]) -> Rule | None:
    """Primera regla cuyo patron aparezca en la descripcion."""
    texto = normalize(description)
    if not texto:
        return None
    for rule in rules:
        patron = rule.normalized
        if patron and patron in texto:
            return rule
    return None


def categorize(description: str, rules: list[Rule]) -> tuple[str, str] | None:
    """Devuelve (categoria, origen) o None si ninguna regla aplica."""
    encontrada = match(description, rules)
    if encontrada is None:
        return None
    origen = SOURCE_AI if encontrada.source == SOURCE_AI else SOURCE_RULE
    return encontrada.category, origen


def is_uncategorized(category: str | None) -> bool:
    return not category or normalize(category) == normalize(UNCATEGORIZED)

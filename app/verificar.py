"""Etapa de verificacion: cada cifra de la respuesta tiene que salir de los datos.

El modelo puede inventar un numero con dos decimales que parece sacado de
una consulta. Esto lo pilla por fuerza bruta: se juntan TODOS los numeros
que devolvieron las herramientas y el resumen (la "evidencia"), se sacan
los numeros que aparecen en la respuesta, y cada uno tiene que ser

- un numero de la evidencia (con la precision con la que esta escrito),
- la suma o la diferencia de dos,
- un porcentaje a/b de dos, o un cociente,
- o una redondeo razonable de cualquiera de los anteriores.

Lo que no cuadra se devuelve para que el modelo lo corrija o, si insiste,
para avisar al usuario. Es deliberadamente simple: prefiere avisar de mas
a dejar pasar una cifra inventada.
"""
from __future__ import annotations

import bisect
import itertools
import math
import re
from typing import Any, Iterable

# Numero con miles y decimales en formato es-AR (1.234.567,89) o en ingles
# (1,234,567.89), o sin miles. Porcentaje opcional.
NUM_RE = re.compile(
    r"(?<![\w/.,-])"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
    r"\s*(?P<pct>%)?"
    r"(?![\w/])"
)
FECHA_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|\b\d{4}-\d{2}(?:-\d{2})?\b")
ANO_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _a_numero(texto: str) -> float | None:
    t = texto.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", t):
        t = t.replace(",", "")
    elif "," in t and "." not in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _decimales(texto: str) -> int:
    t = texto.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t) or re.fullmatch(r"\d{1,3}(?:,\d{3})+", t):
        return 0
    m = re.search(r"[.,](\d+)$", t)
    return len(m.group(1)) if m else 0


def cifras_en(texto: str) -> list[dict[str, Any]]:
    """Numeros que afirma la respuesta, con su texto y si es porcentaje.

    Se saltan fechas, anos y los numeros de las listas ("1." al principio).
    Las cifras dentro de un bloque ```chart cuentan: son datos.
    """
    limpio = FECHA_RE.sub(" ", texto)
    limpio = ANO_RE.sub(" ", limpio)
    limpio = re.sub(r"(?m)^\s*\d+[.)]\s", " ", limpio)
    salida = []
    for m in NUM_RE.finditer(limpio):
        crudo = m.group("num")
        valor = _a_numero(crudo)
        if valor is None:
            continue
        es_pct = bool(m.group("pct"))
        # Un numero suelto de una cifra ("3 cosas", "2 personas") no es una
        # afirmacion sobre los datos que valga la pena verificar.
        if not es_pct and valor < 10 and _decimales(crudo) == 0:
            continue
        salida.append({"texto": crudo + ("%" if es_pct else ""), "valor": valor, "pct": es_pct, "decimales": _decimales(crudo)})
    return salida


def numeros_en(dato: Any) -> set[float]:
    """Todos los numeros de una estructura JSON (resultados de herramientas)."""
    encontrados: set[float] = set()

    def visitar(x: Any) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            if math.isfinite(x):
                encontrados.add(round(float(x), 2))
        elif isinstance(x, dict):
            for v in x.values():
                visitar(v)
        elif isinstance(x, (list, tuple, set)):
            for v in x:
                visitar(v)
        elif isinstance(x, str):
            # Importes escritos como texto en las descripciones no cuentan.
            return

    visitar(dato)
    return encontrados


def _coincide(afirmado: float, candidato: float, decimales: int, pct: bool = False) -> bool:
    if pct:
        return abs(afirmado - candidato) <= 0.15 if decimales else abs(afirmado - candidato) <= 0.6
    # La cifra puede estar redondeada a los decimales con los que se escribio.
    tolerancia = 0.5 * 10 ** (-decimales) + 1e-9
    if abs(afirmado - candidato) <= tolerancia:
        return True
    # "8,3 millones", "48 mil": la cifra escrita es el candidato dividido.
    for escala in (1_000, 1_000_000):
        if abs(afirmado * escala - candidato) <= tolerancia * escala:
            return True
    return False


def verificar(respuesta: str, evidencia: Iterable[float], max_combinaciones: int = 400) -> list[str]:
    """Cifras de la respuesta que no salen de la evidencia.

    Devuelve los textos tal como aparecen ("8.284.184,76", "85,7%"), para
    poder decirle al modelo exactamente que corregir.
    """
    base = sorted({round(float(v), 2) for v in evidencia if math.isfinite(float(v))})
    if not base:
        return [c["texto"] for c in cifras_en(respuesta)]

    # Para las combinaciones de a dos se usan solo los valores mas grandes:
    # las sumas que importan son de totales, no de centavos. Todo se
    # precalcula una vez en listas ordenadas y cada cifra se busca con
    # bisect alrededor de su valor: asi una respuesta con treinta numeros
    # no tarda segundos.
    grandes = sorted(base, key=abs, reverse=True)[:max_combinaciones]
    directos = sorted({abs(v) for v in base})
    derivados: set[float] = set()
    porcentajes: set[float] = set()
    for a, b in itertools.combinations(grandes, 2):
        derivados.add(round(abs(a + b), 2))
        derivados.add(round(abs(a - b), 2))
    for a, b in itertools.permutations(grandes, 2):
        if b:
            derivados.add(round(abs(a / b), 4))
            porcentajes.add(round(abs(a) / abs(b) * 100, 2))
            porcentajes.add(round(abs((a - b) / b) * 100, 2))
    derivados_ord = sorted(derivados)
    porcentajes_ord = sorted(porcentajes | {abs(v) for v in base})

    def cerca(valores: list[float], v: float, holgura: float) -> list[float]:
        i = bisect.bisect_left(valores, v - holgura)
        j = bisect.bisect_right(valores, v + holgura)
        return valores[i:j]

    def justificada(c: dict[str, Any]) -> bool:
        v, d, pct = c["valor"], c["decimales"], c["pct"]
        if pct:
            return any(_coincide(v, x, d, True) for x in cerca(porcentajes_ord, v, 1.0))
        holgura = 0.5 * 10 ** (-d) + 1e-9
        for escala in (1, 1_000, 1_000_000):
            # "0,9 millones": la cifra escrita es el valor real dividido.
            objetivo = v * escala
            for lista in (directos, derivados_ord):
                if any(_coincide(v, x, d) for x in cerca(lista, objetivo, holgura * escala)):
                    return True
        return False

    vistas: set[str] = set()
    sin_respaldo = []
    for c in cifras_en(respuesta):
        if c["texto"] in vistas:
            continue
        vistas.add(c["texto"])
        if not justificada(c):
            sin_respaldo.append(c["texto"])
    return sin_respaldo


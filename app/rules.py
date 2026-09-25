"""Aplicacion de las reglas de categorizacion sobre los movimientos."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.categorize import (
    NOT_A_MOVEMENT,
    SOURCE_AI,
    SOURCE_FILE,
    SOURCE_MANUAL,
    SOURCE_NONE,
    SOURCE_RULE,
    UNCATEGORIZED,
    Rule,
    categorize,
    default_rules,
    is_not_movement,
    is_uncategorized,
    normalize,
    sort_rules,
)
from app.models import CategoryRule, Transaction, UploadedFile

# Origenes que el recategorizado puede sobrescribir. Lo que el usuario
# corrigio a mano y lo que vino con categoria en el archivo se respetan.
REPLACEABLE = {SOURCE_NONE, SOURCE_RULE, SOURCE_AI}


def load_rules(db: Session, include_defaults: bool = True) -> list[Rule]:
    """Reglas guardadas y, si se piden, las de fabrica.

    Las guardadas son dos cosas a la vez: lo que el usuario corrigio a mano
    y la **cache** de lo que el modelo ya respondio. Por eso se consultan
    siempre antes de volver a preguntar.
    """
    propias = [
        Rule(pattern=r.pattern, category=r.category, source=r.source or "manual")
        for r in db.execute(select(CategoryRule)).scalars().all()
    ]
    if not include_defaults:
        return sort_rules(propias)
    return sort_rules(propias + default_rules())


def categorize_rows(rows: list[dict], rules: list[Rule]) -> int:
    """Rellena la categoria de las filas recien leidas de un archivo.

    Solo toca las que no traen categoria propia: si el archivo la tiene,
    manda el archivo.
    """
    aplicadas = 0
    for row in rows:
        if not is_uncategorized(row.get("category")):
            row["category_source"] = SOURCE_FILE
            continue
        resultado = categorize(row.get("description", ""), rules)
        if resultado is None:
            row["category"] = UNCATEGORIZED
            row["category_source"] = SOURCE_NONE
            continue
        row["category"], row["category_source"] = resultado
        aplicadas += 1
    return aplicadas


def recategorize(db: Session, only_uncategorized: bool = False) -> int:
    """Vuelve a aplicar las reglas a los movimientos ya guardados."""
    rules = load_rules(db)
    stmt = select(Transaction).where(
        Transaction.category_source.in_(REPLACEABLE) | Transaction.category_source.is_(None)
    )
    cambiados = 0
    for tx in db.execute(stmt).scalars().all():
        if only_uncategorized and not is_uncategorized(tx.category):
            continue
        resultado = categorize(tx.description or "", rules)
        if resultado is None:
            continue
        categoria, origen = resultado
        if is_not_movement(categoria):
            # Descartar es borrar, y borrar no puede ser un efecto colateral
            # de "recategorizar". Lo hace drop_non_movements, que se llama
            # donde el usuario espera que algo desaparezca.
            continue
        if tx.category == categoria and tx.category_source == origen:
            continue
        tx.category = categoria
        tx.category_source = origen
        cambiados += 1
    db.commit()
    return cambiados


def apply_rule(db: Session, pattern: str, category: str) -> int:
    """Aplica un patron concreto a todos los movimientos que lo contengan."""
    patron = normalize(pattern)
    if not patron:
        return 0
    cambiados = 0
    for tx in db.execute(select(Transaction)).scalars().all():
        if tx.category_source == SOURCE_MANUAL:
            continue
        if patron in normalize(tx.description or ""):
            tx.category = category
            tx.category_source = SOURCE_RULE
            cambiados += 1
    db.commit()
    return cambiados


def refresh_counts(db: Session, file_ids: set[int]) -> None:
    """Recalcula cuantos movimientos le quedan a cada archivo.

    El numero se muestra en la pantalla de Archivos; si no se actualiza al
    borrar, dice mas de los que hay.
    """
    for file_id in file_ids:
        record = db.get(UploadedFile, file_id)
        if record is None:
            continue
        record.row_count = (
            db.execute(
                select(func.count(Transaction.id)).where(Transaction.file_id == file_id)
            ).scalar()
            or 0
        )


def _delete_where(db: Session, coincide) -> int:
    borrados = 0
    archivos: set[int] = set()
    for tx in db.execute(select(Transaction)).scalars().all():
        if coincide(normalize(tx.description or "")):
            if tx.file_id:
                archivos.add(tx.file_id)
            db.delete(tx)
            borrados += 1
    if borrados:
        db.flush()
        refresh_counts(db, archivos)
    db.commit()
    return borrados


def delete_matching(db: Session, pattern: str) -> int:
    """Borra los movimientos cuya descripcion contenga ese patron."""
    patron = normalize(pattern)
    if not patron:
        return 0
    return _delete_where(db, lambda descripcion: patron in descripcion)


def drop_non_movements(db: Session) -> int:
    """Borra lo que alguna regla marca como 'no es un movimiento'.

    Las lineas de totales y saldos no son gastos: si entran, inflan el
    total. Cuales son no se puede saber de antemano (cada banco las
    escribe distinto), asi que lo decide el modelo una vez por descripcion
    y queda guardado como regla. Esto aplica esa decision a lo que ya esta
    importado y a lo que se importe despues.
    """
    patrones = [
        r.pattern
        for r in db.execute(select(CategoryRule)).scalars().all()
        if is_not_movement(r.category)
    ]
    if not patrones:
        return 0
    normalizados = [normalize(p) for p in patrones if normalize(p)]
    if not normalizados:
        return 0
    return _delete_where(
        db, lambda descripcion: any(p in descripcion for p in normalizados)
    )


def save_rule(db: Session, pattern: str, category: str, source: str = "manual") -> CategoryRule:
    """Guarda (o actualiza) una regla para ese patron."""
    patron = (pattern or "").strip()
    if not patron:
        raise ValueError("El patron no puede estar vacio")
    categoria = (category or "").strip() or UNCATEGORIZED

    existente = next(
        (
            r
            for r in db.execute(select(CategoryRule)).scalars().all()
            if normalize(r.pattern) == normalize(patron)
        ),
        None,
    )
    if existente:
        existente.category = categoria
        existente.source = source
        db.commit()
        return existente

    regla = CategoryRule(pattern=patron, category=categoria, source=source)
    db.add(regla)
    db.commit()
    return regla


def uncategorized_descriptions(db: Session, limit: int = 200) -> list[tuple[str, int, float]]:
    """Descripciones distintas sin categorizar, con cuantas veces aparecen.

    Sirve tanto para la pantalla de reglas como para pedirle sugerencias al
    modelo: agrupar por descripcion evita mandarle cientos de lineas iguales.
    """
    agrupadas: dict[str, dict] = {}
    for tx in db.execute(select(Transaction)).scalars().all():
        if not is_uncategorized(tx.category):
            continue
        clave = (tx.description or "").strip()
        if not clave:
            continue
        entrada = agrupadas.setdefault(clave, {"veces": 0, "total": 0.0})
        entrada["veces"] += 1
        entrada["total"] += abs(float(tx.amount or 0.0))

    ordenadas = sorted(agrupadas.items(), key=lambda kv: kv[1]["total"], reverse=True)
    return [(desc, datos["veces"], round(datos["total"], 2)) for desc, datos in ordenadas[:limit]]


# Cuantas descripciones por llamada. Trocear evita dos cosas: que la
# respuesta se corte por el limite de tokens, y que un fallo se lleve
# puesto el lote entero en vez de un trozo.
AI_CHUNK = 50


async def ai_categorize_pending(db: Session, limit: int = 300) -> tuple[int, int, int]:
    """Pregunta al modelo por las descripciones sin categorizar.

    Se le mandan solo las descripciones DISTINTAS, con su importe y si son
    gasto o ingreso, y cada respuesta se guarda como regla: la proxima vez
    que aparezca ese comercio ya no hace falta preguntar. Devuelve
    (reglas creadas, movimientos actualizados, movimientos descartados).

    El modelo tambien puede contestar que una descripcion no es un
    movimiento (un total, un saldo, una cabecera repetida). Esa respuesta
    se guarda igual que cualquier otra y esas lineas se borran: es la
    alternativa a mantener a mano una lista de como escribe sus totales
    cada banco.

    Si falla un trozo se sigue con los demas; solo se propaga el error
    cuando no se pudo clasificar nada.
    """
    from app.llm import OpenRouterError, suggest_categories  # diferido: evita un ciclo

    pendientes = [
        {
            "descripcion": fila["descripcion"],
            "importe": fila["promedio"],
            "es_gasto": fila["es_gasto"],
        }
        for fila in description_summary(db, only_uncategorized=True, limit=limit)
    ]
    if not pendientes:
        return 0, 0, 0

    creadas = 0
    aplicados = 0
    descartados = 0
    errores: list[str] = []
    for inicio in range(0, len(pendientes), AI_CHUNK):
        trozo = pendientes[inicio : inicio + AI_CHUNK]
        try:
            sugerencias = await suggest_categories(trozo)
        except OpenRouterError as exc:
            errores.append(str(exc))
            continue
        for descripcion, categoria in sugerencias.items():
            categoria = (categoria or "").strip()
            if not categoria or is_uncategorized(categoria):
                continue
            descartar = is_not_movement(categoria)
            try:
                save_rule(
                    db,
                    descripcion,
                    NOT_A_MOVEMENT if descartar else categoria,
                    source=SOURCE_AI,
                )
            except ValueError:
                continue
            creadas += 1
            if descartar:
                descartados += delete_matching(db, descripcion)
            else:
                aplicados += apply_rule(db, descripcion, categoria)

    if errores and not creadas:
        raise OpenRouterError(errores[0])
    return creadas, aplicados, descartados


async def ai_review_non_movements(db: Session, limit: int = 400) -> tuple[int, list[str]]:
    """Revisa TODAS las descripciones buscando lineas que no son movimientos.

    Existe porque el paso de la importacion solo le pregunta al modelo por
    lo que quedo sin categoria: una linea de totales que ya habia recibido
    una categoria (por una regla vieja, o antes de que esto existiera) no
    se revisa sola nunca mas. Esto la encuentra.

    Devuelve (movimientos borrados, descripciones descartadas).
    """
    from app.llm import OpenRouterError, find_non_movements  # diferido: evita un ciclo

    filas = [
        {"descripcion": f["descripcion"], "importe": f["promedio"], "veces": f["veces"]}
        for f in description_summary(db, limit=limit)
        # Lo que el usuario corrigio a mano no se toca: ya dijo que es.
        if f["origen"] != SOURCE_MANUAL
    ]
    if not filas:
        return 0, []

    descartadas: list[str] = []
    errores: list[str] = []
    for inicio in range(0, len(filas), AI_CHUNK):
        trozo = filas[inicio : inicio + AI_CHUNK]
        try:
            respuesta = await find_non_movements(trozo)
        except OpenRouterError as exc:
            errores.append(str(exc))
            continue
        conocidas = {f["descripcion"] for f in trozo}
        # Solo se acepta lo que salio de la lista enviada: si el modelo
        # inventa o reformula una descripcion, no se borra nada.
        descartadas.extend(d for d in respuesta if d in conocidas)

    if errores and not descartadas:
        raise OpenRouterError(errores[0])

    borrados = 0
    for descripcion in descartadas:
        try:
            save_rule(db, descripcion, NOT_A_MOVEMENT, source=SOURCE_AI)
        except ValueError:
            continue
        borrados += delete_matching(db, descripcion)
    return borrados, descartadas


ORIGIN_LABELS = {
    SOURCE_MANUAL: "corregida a mano",
    SOURCE_AI: "el modelo",
    SOURCE_RULE: "una regla",
    SOURCE_FILE: "venia en el archivo",
    SOURCE_NONE: "—",
}


def description_summary(
    db: Session, only_uncategorized: bool = False, limit: int = 300
) -> list[dict]:
    """Descripciones distintas con su categoria y QUIEN la decidio.

    Ver el origen es lo que permite entender por que algo quedo mal: si
    todo dice "una regla", el modelo no esta interviniendo.
    """
    agrupadas: dict[str, dict] = {}
    for tx in db.execute(select(Transaction)).scalars().all():
        clave = (tx.description or "").strip()
        if not clave:
            continue
        if only_uncategorized and not is_uncategorized(tx.category):
            continue
        entrada = agrupadas.setdefault(
            clave,
            {
                "descripcion": clave,
                "veces": 0,
                "total": 0.0,
                "neto": 0.0,
                "categoria": tx.category or UNCATEGORIZED,
                "origen": tx.category_source or SOURCE_NONE,
            },
        )
        importe = float(tx.amount or 0.0)
        entrada["veces"] += 1
        entrada["total"] += abs(importe)
        entrada["neto"] += importe

    ordenadas = sorted(agrupadas.values(), key=lambda e: e["total"], reverse=True)
    for entrada in ordenadas:
        entrada["total"] = round(entrada["total"], 2)
        entrada["promedio"] = round(entrada["total"] / max(entrada["veces"], 1), 2)
        entrada["es_gasto"] = entrada["neto"] >= 0
        entrada["origen_label"] = ORIGIN_LABELS.get(entrada["origen"], entrada["origen"])
        entrada["pendiente"] = is_uncategorized(entrada["categoria"])
    return ordenadas[:limit]

# Gastos — dashboard + chat

App web sencilla, con usuario y contraseña, para subir archivos de gastos
(CSV / Excel), decidir cuáles están **activos** en cada momento, verlos en un
**dashboard** con gráficos (por persona, por categoría, por mes…) y seguir
explorándolos en un **chat** conectado a **OpenRouter**.

Pensada para desplegarse en **Railway** con un par de variables de entorno.

---

## Qué hace

| Pantalla | Para qué sirve |
|---|---|
| **/archivos** | Subir CSV/TSV/Excel/PDF, activar o desactivar cada archivo, corregir el mapeo de columnas, descargar o borrar. |
| **/** (dashboard) | KPIs, evolución mensual, gasto por categoría, por persona, cruce persona × categoría, gasto por día de la semana, mayores gastos, tabla de movimientos, exportación a CSV. |
| **/chat** | Preguntas en lenguaje natural sobre los datos activos. El modelo puede devolver gráficos que se dibujan en la conversación. |
| **/categorias** | Reglas de categorización, lo que quedó sin categorizar y sugerencias del modelo. |
| **/ajustes** | Moneda base, modo de conversión y tipos de cambio (de una API o a mano). |

Solo los archivos marcados como **activos** entran en el dashboard y en el chat,
así que puedes tener varios exports subidos y combinarlos o aislarlos sin borrar nada.

## Categorías

Un extracto bancario no trae categoría: trae `COMPRA COTO DIGITAL` y poco más.
Así que se deduce de la descripción, con reglas de texto simples y explicables.

La app trae **reglas de fábrica pensadas para Argentina**: cadenas de
supermercado, estaciones de servicio, peajes, prepagas y farmacias, servicios
(Edenor, Metrogas, Aysa, telefonía), suscripciones, impuestos (AFIP, ARBA, ABL,
ley 25413), comisiones bancarias, cajeros y sueldos.

Tres formas de completar lo que falte, de menos a más automática:

1. **A mano en la tabla** — en el dashboard, un click en la categoría la
   convierte en un selector. Al guardar podés marcar *"aplicar a todos los que
   digan lo mismo"*, que además crea una regla para los archivos futuros.
2. **Desde /categorias** — lista lo que quedó sin categorizar agrupado por
   descripción y ordenado por importe, para asignar en bloque lo que más pesa.
3. **Sugerir con IA** — manda al modelo solo las descripciones distintas que
   ninguna regla reconoció y crea una regla por cada una, para que las revises.
   Necesita `OPENROUTER_API_KEY`.

Dos garantías: **lo que corregís a mano no se vuelve a pisar** al recategorizar,
y **si el archivo trae categoría, manda el archivo**. Entre reglas gana la más
específica, salvo unas pocas que tienen prioridad explícita porque la longitud
engaña: `TRANSFERENCIA RECIBIDA SUELDO` es un ingreso, no una transferencia.

## Resúmenes en PDF

Además de CSV y Excel se aceptan **PDFs de banco y tarjeta**. Se intentan dos
lecturas: primero las tablas reales del PDF, y si no hay, las líneas de texto
buscando el patrón habitual de un resumen argentino (fecha al principio,
descripción, e importe al final).

Lo que hace bien:

- Fechas sin año (`15/03`), tomando el año de la cabecera del documento.
- Líneas con **importe y saldo** en la misma fila: se queda con el movimiento,
  no con el saldo acumulado.
- Descarta arrastres de saldo y totales (`SALDO ANTERIOR`, `SUBTOTAL`…), que si
  no inflarían el gasto con dinero que nunca se movió.
- Detecta `US$` y `U$S` por consumo, así que un resumen de tarjeta con compras
  en pesos y en dólares se importa con cada moneda en su sitio.

Límites honestos: **un PDF escaneado no sirve** (no tiene capa de texto; la app
lo dice en vez de importar basura), y cada banco maqueta distinto. Si tu resumen
no sale bien, el mapeo de columnas se corrige a mano desde Archivos, o exportá
el CSV desde el homebanking, que siempre es más fiable.

## Formato de los archivos

No hay un formato obligatorio: al subir un archivo se detectan las columnas por
su nombre (en español o inglés) y se normalizan fechas e importes.

- **Fecha**: `fecha`, `fecha operacion`, `date`, `transaction date`…
- **Importe**: `importe`, `monto`, `cantidad`, `amount`, `total`…
- **Categoría**: `categoria`, `category`, `rubro`, `tipo de gasto`…
- **Persona**: `persona`, `quien`, `pagador`, `paid by`, `titular`…
- **Descripción**: `concepto`, `descripcion`, `comercio`, `merchant`…
- Opcionales: `cuenta`/`tarjeta`, `moneda`, y `tipo` (ingreso/gasto).

Se aceptan importes en formato español (`1.234,56`), inglés (`1,234.56`),
entre paréntesis (`(89,90)` = negativo) y con símbolo de moneda.

## Monedas (pesos y dólares)

La moneda base se elige en **/ajustes** (`CURRENCY` solo da el valor inicial,
así que no hace falta redesplegar para cambiarla). Cada movimiento guarda
**su propia** moneda, que se detecta así, por orden:

1. Una columna de moneda (`moneda`, `divisa`, `currency`) — acepta `USD`, `dólares`, `pesos`…
2. Un símbolo dentro del propio importe: `US$ 120,50` o `U$S 120,50` → dólares.
   Ojo: `$` **a secas se interpreta como la moneda del archivo**, que en Argentina
   normalmente es pesos.
3. La moneda que elijas para ese archivo al subirlo (por defecto, la base).

Con eso, **nunca se suman importes de monedas distintas**. En el dashboard el
selector *Moneda* ofrece:

- **Una moneda concreta** — muestra solo esos movimientos, con sus importes tal cual.
- **Todo en ARS** — convierte cada importe con el tipo de cambio que tengas cargado.
  Si falta el de alguna moneda, el dashboard **no inventa el total**: avisa, muestra
  solo la moneda mayoritaria y te enlaza a Ajustes.

La tabla de movimientos y el CSV exportado siempre llevan el importe y la moneda
originales, sin convertir.

### Cotizaciones

En **/ajustes**, cada moneda se puede traer de una API o escribir a mano:

| Origen | Qué cotiza |
|---|---|
| [dolarapi.com](https://dolarapi.com) | Contra el peso argentino: dólar **oficial, blue, MEP, CCL, tarjeta, mayorista y cripto**, y además euro, real, peso chileno y uruguayo. |
| [open.er-api.com](https://open.er-api.com) | Cualquier otro par, como respaldo genérico. |

Ninguna de las dos necesita clave ni registro.

El origen se elige por moneda y queda guardado, así que el botón **Actualizar
cotizaciones** refresca todo con el mismo criterio. Detalles que importan:

- Para el dólar se usa el valor de **venta** (lo que te cuesta comprarlo), y el
  mensaje de confirmación muestra también la compra.
- Un tipo escrito **a mano nunca se pisa** al actualizar.
- Si la API falla, **se conserva el valor anterior** y se avisa del error.
- Junto a cada tipo se ve de dónde salió y hace cuánto se actualizó.

### Cuándo se convierte: hoy o el día del gasto

En Ajustes se elige entre dos modos, y la diferencia es grande con inflación alta:

- **Tipo de cambio actual** — todo al valor de hoy. Responde "cuánto valdría hoy
  lo que gasté".
- **Tipo del día de cada movimiento** — cada gasto al valor que regía el día que
  ocurrió. Es el que hay que usar para **comparar meses entre sí**: convertir
  enero al dólar de hoy aplana la evolución y hace que un gasto que subió parezca
  constante.

Para el segundo modo hay que descargar el histórico de cada moneda (un botón por
moneda, que pide solo el período que cubren tus movimientos). Sale de
[api.argentinadatos.com](https://argentinadatos.com) para el dólar contra el peso
—con la misma casa que elijas: blue, oficial, MEP…— y de
[frankfurter.app](https://frankfurter.app) para el resto.

Si un movimiento cae en fin de semana o feriado se usa la última cotización
anterior. Lo que no tenga histórico cae al tipo actual, y el dashboard dice
cuántos movimientos fueron en vez de disimularlo.

**Convención de signo**: internamente un importe **positivo es un gasto** y uno
**negativo es un ingreso**. En un extracto bancario (donde los gastos vienen en
negativo) el signo se invierte automáticamente; si se equivoca, se corrige en
*Archivos → Mapeo de columnas → Signo* y se reimporta.

Si la detección falla, el desplegable de cada campo permite elegir la columna
correcta y volver a importar ese archivo sin tocar los demás.

## Variables de entorno

Copia `.env.example` a `.env` para desarrollo local. En Railway se ponen en
*Variables*.

| Variable | Obligatoria | Para qué |
|---|---|---|
| `APP_USERNAME` | sí | Usuario del login. |
| `APP_PASSWORD` | sí | Contraseña del login. |
| `SECRET_KEY` | sí | Firma la cookie de sesión. Usa algo largo y aleatorio. |
| `OPENROUTER_API_KEY` | para el chat | Clave de [OpenRouter](https://openrouter.ai/keys). Sin ella el dashboard funciona y el chat aparece desactivado. |
| `OPENROUTER_MODEL` | no | Modelo por defecto (`anthropic/claude-sonnet-4.5`). |
| `DATA_DIR` | recomendada | Carpeta de datos (SQLite + archivos subidos). En Railway: la ruta del volumen, p. ej. `/data`. |
| `DATABASE_URL` | no | Si la defines (p. ej. Postgres de Railway) se usa en vez de SQLite. |
| `CURRENCY` | no | Moneda base **inicial** (`ARS` por defecto); después manda lo que elijas en /ajustes. |
| `LOCALE` | no | Formato de números y fechas (`es-AR` por defecto). |
| `SESSION_MAX_AGE` | no | Duración de la sesión en segundos (7 días por defecto). |
| `MAX_UPLOAD_MB` | no | Tamaño máximo por archivo (25 MB por defecto). |

## Desplegar en Railway

1. Sube este repo a GitHub y en Railway elige **New Project → Deploy from GitHub repo**.
   Nixpacks detecta Python y usa el `Procfile` / `railway.json`.
2. En **Variables**, añade al menos `APP_USERNAME`, `APP_PASSWORD`, `SECRET_KEY`
   y `OPENROUTER_API_KEY`.
3. **Persistencia** (importante: el disco del contenedor se borra en cada deploy):
   - *Opción A (simple)*: crea un **Volume** montado en `/data` y pon `DATA_DIR=/data`.
     Los archivos subidos y la base SQLite viven ahí. **Sin volumen se pierde todo
     en cada deploy**, así que no es opcional si quieres conservar los archivos.
   - *Opción B*: añade el plugin **Postgres**; Railway inyecta `DATABASE_URL` y la app
     lo usa automáticamente. Aun así conviene un volumen para los archivos originales
     (hacen falta para reimportar tras cambiar el mapeo).
4. Railway asigna `PORT` solo; el healthcheck apunta a `/healthz`.

## Desarrollo local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # edita usuario, contraseña y clave de OpenRouter
uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

Tests:

```bash
pip install pytest
python -m pytest -q
```

## Estructura

```
app/
  main.py         arranque de FastAPI, rutas de error, healthcheck
  config.py       variables de entorno
  db.py           motor SQLAlchemy (SQLite o Postgres)
  models.py       UploadedFile, Transaction, ChatMessage
  security.py     login de usuario único con cookie firmada
  ingest.py       lectura de CSV/Excel y normalización a transacciones
  analytics.py    agregaciones (por categoría, persona, mes, cruce…) y conversión de moneda
  categorize.py   reglas de categorización y su orden de aplicación
  rules.py        aplicar reglas a los movimientos guardados
  currency.py     detección y normalización de monedas (ARS, USD, `US$`, `U$S`…)
  rates.py        cotizaciones del día e históricas (dolarapi, er-api, argentinadatos, frankfurter)
  pdf_import.py   lectura de resúmenes bancarios en PDF
  preferences.py  preferencias guardadas en la base (moneda base, modo de conversión)
  llm.py          cliente de OpenRouter y contexto de datos del chat
  routers/        auth, archivos, dashboard, chat, categorias, ajustes
  templates/      Jinja2
  static/         CSS, JS de gráficos y Chart.js (incluido en el repo)
tests/            pruebas de ingesta, PDF, monedas, categorías y de la app completa
```

## Notas

- **Seguridad**: es un login de usuario único pensado para uso personal. La
  contraseña se compara con la de la variable de entorno, la sesión va en una
  cookie firmada (`HttpOnly`, `Secure` en producción). No hay registro ni
  recuperación de contraseña.
- **Privacidad del chat**: al preguntar, se envía a OpenRouter un **resumen
  agregado** de los datos activos (totales por categoría, persona y mes, mayores
  gastos y una muestra de los movimientos recientes), no el archivo entero.
- **Gráficos**: Chart.js se sirve desde el propio repo (`app/static/vendor/`),
  sin depender de un CDN. La paleta está elegida para ser legible en modo claro
  y oscuro y distinguible con daltonismo.

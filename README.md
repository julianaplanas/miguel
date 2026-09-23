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
| **/archivos** | Subir CSV/TSV/Excel, activar o desactivar cada archivo, corregir el mapeo de columnas, descargar o borrar. |
| **/** (dashboard) | KPIs, evolución mensual, gasto por categoría, por persona, cruce persona × categoría, gasto por día de la semana, mayores gastos, tabla de movimientos, exportación a CSV. |
| **/chat** | Preguntas en lenguaje natural sobre los datos activos. El modelo puede devolver gráficos que se dibujan en la conversación. |
| **/ajustes** | Moneda base y tipos de cambio (traídos de una API o escritos a mano). |

Solo los archivos marcados como **activos** entran en el dashboard y en el chat,
así que puedes tener varios exports subidos y combinarlos o aislarlos sin borrar nada.

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
  currency.py     detección y normalización de monedas (ARS, USD, `US$`, `U$S`…)
  rates.py        cotizaciones automáticas (dolarapi y open.er-api)
  preferences.py  preferencias guardadas en la base (moneda base)
  llm.py          cliente de OpenRouter y contexto de datos del chat
  routers/        auth, archivos, dashboard, chat, ajustes
  templates/      Jinja2
  static/         CSS, JS de gráficos y Chart.js (incluido en el repo)
tests/            pruebas de ingesta, monedas y de la app completa
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

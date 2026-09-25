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
Así que se deduce de la descripción.

**Por defecto decide el modelo** (si hay `OPENROUTER_API_KEY`). Al importar, cada
descripción que no se haya visto antes se le pregunta, y **la respuesta se guarda
como regla**: ese comercio no se vuelve a preguntar nunca más. Es una llamada por
importación, con las descripciones distintas agrupadas — no una por línea.

El orden al importar es:

1. **Tus correcciones a mano** — siempre mandan.
2. **Reglas guardadas** — la caché de lo que ya respondió el modelo y lo que
   corregiste antes.
3. **El modelo** — para lo que nunca se vio.
4. **Reglas de fábrica** — para lo que el modelo no supo, o cuando no hay clave.

Las de fábrica son una lista pensada para Argentina (cadenas de supermercado,
estaciones de servicio, peajes, prepagas, servicios, impuestos, comisiones
bancarias, sueldos). Sirven como respaldo sin red, no como la vía de crecimiento:
para un comercio nuevo no hace falta tocar código.

Entre reglas gana la más específica, pero la longitud del texto engaña en los dos
sentidos, así que hay dos excepciones:

- **Prioridad alta** para palabras cortas que definen el movimiento: `sueldo`,
  `alquiler`, `expensas`. Sin esto, `TRANSFERENCIA RECIBIDA SUELDO` sería una
  transferencia en vez de un ingreso.
- **Prioridad baja** para las que describen el *mecanismo* y no el propósito:
  `transferencia`, `debin`, `visa`, `mastercard`. Aparecen en casi todas las
  líneas de un extracto argentino, así que si ganaran por ser largas se comerían
  el resumen entero: `PAGO TRANSFERENCIA EDENOR` es un servicio, y
  `COMPRA VISA DEBITO COTO` es el supermercado.

En **/categorias** la columna *Decidió* muestra de dónde salió cada categoría
(una regla, el modelo, tu corrección o el archivo), y un aviso arriba dice qué
estrategia está activa — útil cuando algo queda mal y no sabés por qué. El botón
**Probar el modelo** hace una llamada real y muestra qué contestó o qué error dio,
para descartar clave mal puesta, modelo inexistente o falta de crédito.

Al modelo se le manda, por cada descripción distinta, si es gasto o ingreso y su
importe típico: sin eso no puede distinguir un alquiler de un kiosco. Los pedidos
van en tandas de 50, así que un fallo no se lleva puesto el lote entero.

En Ajustes se puede cambiar a **solo reglas**, que no consulta al modelo.

Para corregir:

- **En la tabla del dashboard** — un click en la categoría la convierte en
  selector. Al guardar podés marcar *"aplicar a todos los que digan lo mismo"*,
  que además crea una regla para los archivos futuros.
- **En /categorias** — lo que quedó sin categorizar, agrupado por descripción y
  ordenado por importe, para asignar en bloque lo que más pesa. También están
  todas las reglas guardadas, para borrar las que no te convenzan.

Dos garantías: **lo que corregís a mano no se vuelve a pisar** al recategorizar,
y **si el archivo trae categoría, manda el archivo**. Si el modelo falla, la
importación no se pierde: los movimientos quedan guardados y se cae a las reglas.

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

De los resúmenes de tarjeta con columnas separadas de **pesos y dólares** se
lee la columna donde cae cada importe, no el orden del texto: un consumo en
dólares queda en dólares aunque la descripción diga otra cosa. El pago del
resumen anterior no se importa (cancela consumos que ya están cargados) y los
cargos sin fecha —comisión de mantenimiento, IVA, percepciones— tampoco, así
que el total importado es el de *consumos del mes*, no el *total a pagar*. Las
cuotas llevan la fecha de la compra original, que es la que imprime el resumen.

Límites honestos: **un PDF escaneado no sirve** (no tiene capa de texto; la app
lo dice en vez de importar basura), y cada banco maqueta distinto. Si tu resumen
no sale bien, el mapeo de columnas se corrige a mano desde Archivos, o exportá
el CSV desde el homebanking, que siempre es más fiable.

Las líneas que no son movimientos —totales, saldos, arrastres, el pago del
propio resumen— **las decide el modelo**, no una lista de palabras: se le
pregunta una vez por descripción y la respuesta queda guardada como regla, así
que esa línea no se vuelve a importar ni a preguntar. Sin `OPENROUTER_API_KEY`
queda un filtro mínimo (lo que empieza por total/saldo/suma). Si se equivoca,
creá una regla a mano con la categoría que corresponda: la regla manual gana.

En Categorías, **Buscar líneas que no son movimientos** hace el repaso completo:
el paso de la importación solo pregunta por lo que quedó sin categoría, así que
un total que ya recibió una categoría no se revisa solo nunca.

**Reprocesar** vuelve a leer los archivos guardados con el lector y las reglas
de hoy, sin tener que borrarlos y subirlos otra vez. Está por archivo y para
todos juntos, arriba en Archivos.

En la tabla de movimientos, la cruz del final de cada fila borra ese
movimiento; en el diálogo se puede marcar **borrar todos los que digan lo
mismo**, que es como suelen aparecer las líneas que no deberían estar. El
borrado es definitivo: para recuperarlas hay que volver a importar el archivo.

## Revisar antes de importar

Subir y ver el resultado en los totales son dos pasos distintos. Al subir, la
casilla **Revisar antes de importar** (marcada por defecto) deja el archivo
leído pero sin importar, y abre una pantalla que muestra exactamente lo que se
va a guardar:

- Los totales por moneda de lo que entraría, para **compararlos con los del
  propio resumen** antes de que se mezclen con el resto.
- Fila por fila, con una casilla por fila: lo que desmarques no entra.
- Las líneas que el lector **vio y descartó** (totales, saldos, el pago del
  propio resumen), que hasta ahora desaparecían en silencio.
- Cómo se mapeó cada columna, editable ahí mismo: se cambia y se vuelve a leer
  sin tocar ningún dato.
- Con `OPENROUTER_API_KEY`, **Revisar con el modelo** le pregunta cuáles de esas
  líneas no son movimientos; lo que marque queda como regla y no vuelve a
  aparecer en ninguna importación futura.

Nada de eso toca la base hasta que pulsás Importar. Si subís varios archivos se
revisan en fila, uno tras otro. Destildando la casilla se importa directo, que
es lo cómodo cuando ya sabés cómo sale ese formato. Un archivo ya importado se
puede volver a revisar desde su tarjeta: se relee con el lector de hoy y podés
reimportarlo con otro mapeo o con otras filas.

## Formato de los archivos

Se pueden subir **varios archivos de una vez** (cada uno queda como una entrada
propia, activable por separado; si uno falla, los demás se importan igual). No
hay un formato obligatorio: se detectan las columnas por su nombre (en español o
inglés) y se normalizan fechas e importes.

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
| `DATA_DIR` | no | Carpeta de datos (SQLite + archivos subidos). En Railway se detecta sola desde el volumen; solo defínela si quieres otra ruta. |
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
   - *Opción A (simple)*: crea un **Volume**. Con eso alcanza: Railway expone su ruta
     en `RAILWAY_VOLUME_MOUNT_PATH` y la app la usa sola. **No hace falta `DATA_DIR`**,
     y si lo defines apuntando fuera del volumen los datos igual se pierden.
     **Sin volumen se pierde todo en cada deploy.**
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

## Dónde se guardan los datos

La carpeta de datos se resuelve en este orden:

1. `DATA_DIR`, si la definiste.
2. `RAILWAY_VOLUME_MOUNT_PATH` — la ruta del volumen, que Railway expone sola.
3. `./data`, que **vive dentro del contenedor y se borra en cada deploy**.

> **No definas `DATA_DIR` en Railway.** Una ruta relativa como `./data` resuelve
> a `/app/data`, que está fuera del volumen: los datos se borran en cada deploy
> aunque el volumen exista. Sin esa variable, la app usa el volumen sola.

En **/ajustes → Almacenamiento** se ve cuál se está usando, de dónde salió, si hay
volumen montado, cuántos archivos hay y si se puede escribir. Si los datos quedaron
fuera del volumen, la app lo dice ahí y también en la pantalla de Archivos, en vez
de dejarte descubrirlo cuando desaparecen.

## Interfaz

La barra superior deja arriba solo lo que se usa a diario —Dashboard, Archivos,
Categorías, Chat— y todo lo demás vive en el menú de usuario, arriba a la
derecha: Ajustes, el tema y salir.

El **tema claro es el que viene por defecto**, independientemente de cómo tenga
configurado el sistema quien abra la app; el oscuro se elige desde ese menú y se
recuerda en el navegador.

Los controles del sistema están reemplazados por componentes propios, en
`app/static/js/ui.js` y sus estilos: avisos apilados que se van solos,
confirmaciones en diálogo (en vez de `confirm()`), desplegables con búsqueda al
escribir y selección múltiple con checks, campo de archivo con arrastrar y
soltar, y un calendario en `dd/mm/aaaa` — el input nativo muestra el formato del
idioma del navegador, que en un Chrome en inglés es `mm/dd/aaaa`.

Todo por **mejora progresiva**: el control nativo sigue en el DOM, oculto, y es
la fuente de verdad. El diseñado se dibuja encima y se sincroniza. Así los
formularios se envían igual, el JS que lee `select.selectedOptions` no cambia, y
si el archivo no carga la app queda fea pero usable. Para reflejar un cambio
hecho por código, basta con disparar un `change` sobre el control nativo.

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

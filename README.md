# Stock Predictor — ingesta de datos bursátiles

Módulo Python 3.12 para descargar, validar, actualizar, consultar y exportar históricos.
No incluye predicciones, indicadores, backtesting ni órdenes. PostgreSQL es el almacenamiento
principal; SQLite es un adaptador para pruebas locales de contratos, no un reemplazo de PostgreSQL.

**Estado de verificación:** ver `docs/VERIFICATION.md`. Los proveedores tienen adaptadores
implementados y pruebas con respuestas controladas. No se afirma verificada la descarga real
cuando el servicio devuelve 429 ni la integración con PostgreSQL cuando no hay servidor.

## Arquitectura

```mermaid
flowchart TD
  CLI[CLI y configuración] --> SI[Servicio de ingesta]
  CLI --> SQ[Servicio de consulta]
  SI --> DP[Puerto de proveedor]
  SI --> RP[Puertos de repositorios y UoW]
  SQ --> RP
  DP --> Y[Yahoo]
  DP --> A[Alpaca]
  DP --> I[Interactive Brokers]
  RP --> PG[SQLAlchemy PostgreSQL]
  RP --> SL[SQLite para contratos]
```

- `domain/`: registros inmutables, tipos y errores propios; precios `Decimal`.
- `ports/contracts.py`: interfaces tipadas de proveedores, seis repositorios y Unit of Work.
- `adapters/providers.py`: Yahoo/Alpaca/IBKR, normalización, capacidades y errores SDK.
- `adapters/schema.py`: esquema SQLAlchemy Core, independiente de entidades del dominio.
- `adapters/persistence.py`: transacciones, consultas, bulk UPSERT por dialecto y auditoría.
- `services/`: calendario, validación, ingesta, lectura, comparación y agregación.
- `migrations/`: Alembic. La migración inicial usa un snapshot congelado del esquema.

La implementación comparte un bundle de repositorios transaccionales sin crear seis clases
con responsabilidades idénticas. Los servicios consumen interfaces de dominio y una fábrica
de UoW, nunca conexiones SQL ni objetos de proveedores. La concurrencia de descarga es **1**
por diseño para el equipo local de 8 GB; se evita paralelismo innecesario y presión sobre las APIs.

## Instalación en Linux Mint 22.3

Desde la carpeta descomprimida:

```bash
sudo apt update
sudo apt install python3.12-venv postgresql postgresql-client
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[yahoo,alpaca,ibkr,test]'
cp .env.example .env
```

La instalación de `python3.12-venv` resuelve el error de `ensurepip` de Ubuntu/Mint.
No se requiere Docker. Las dependencias Yahoo, Alpaca e IBKR son extras independientes.
`docs/tested-versions.txt` registra las versiones directas usadas durante la verificación,
no constituye un lock completo de dependencias transitivas.

### Crear usuario y base PostgreSQL

```bash
sudo systemctl enable --now postgresql
sudo -u postgres psql
```

Dentro de `psql`:

```sql
CREATE ROLE stock_predictor LOGIN;
\password stock_predictor
CREATE DATABASE stock_predictor OWNER stock_predictor;
\q
```

Ingresar en `.env` el usuario y contraseña; si contiene caracteres reservados, codificarlos
para una URL. No copiar credenciales a scripts ni versionarlas.

```env
DATABASE_BACKEND=postgresql
DATABASE_URL=postgresql+psycopg://stock_predictor:TU_PASSWORD@localhost:5432/stock_predictor
```

```bash
alembic upgrade head
python -m stock_predictor.ingestion --help
```

Ejecutar los comandos desde la raíz del proyecto para resolver `.env`, configuración y migraciones.
No hay creación automática de tablas durante una ingesta.

## Configuración

`config/tickers.csv` contiene una única columna `ticker`. Se quitan espacios, se normaliza a
mayúsculas y se eliminan duplicados. Yahoo transforma puntos en guiones en su adaptador
(por ejemplo `BRK.B` → `BRK-B`). Revisar símbolos especiales del proveedor antes de una carga.
Quitar un ticker del CSV deja intactos sus datos e identificadores.

| Variable | Uso |
|---|---|
| DATABASE_BACKEND / DATABASE_URL | Selección y conexión del almacenamiento |
| MARKET_DATA_PROVIDER | `yahoo`, `alpaca` o `ibkr` |
| ALPACA_API_KEY / ALPACA_SECRET_KEY | Credenciales requeridas solo para Alpaca |
| ALPACA_DATA_FEED | `iex` o `sip`; sujeto a autorización |
| IBKR_HOST / IBKR_PORT | TWS o IB Gateway; por defecto `127.0.0.1:7497` |
| IBKR_CLIENT_ID | Identificador de cliente API; por defecto `10` |
| IBKR_TIMEOUT_SECONDS | Tiempo máximo de una solicitud histórica; por defecto `45` |
| IBKR_EXCHANGE / IBKR_CURRENCY | Contrato por defecto; `SMART` y `USD` |
| DEFAULT_HISTORY_YEARS | Historia inicial diaria, por defecto 5 |
| DOWNLOAD_DAILY / DOWNLOAD_INTRADAY | Habilitar cada categoría |
| INTRADAY_INTERVALS | `1m,5m,15m,60m` |
| REGULAR_SESSION_ONLY | `true`: regular; `false`: intradiarias regulares y extendidas separadas |
| BATCH_SIZE | Máximo de registros por operación; se limita también por parámetros SQL |
| MAX_RETRIES / RETRY_BACKOFF_SECONDS | Reintentos temporales y backoff exponencial |
| OVERLAP_DAYS | Ventana de revisión incremental, 7 días por defecto |
| TICKERS_FILE | Ruta CSV |
| LOG_LEVEL | Nivel de logging estándar |

Configuración centralizada con `pydantic-settings`, validación de límites y `.env` en `.gitignore`.

## Carga histórica y actualización

Comenzar por un instrumento y un rango diario pequeño:

```bash
python -m stock_predictor.ingestion backfill --ticker AAPL --interval 1d --start 2026-10-01 --end 2026-10-03
python -m stock_predictor.ingestion backfill --provider yahoo
python -m stock_predictor.ingestion update
python -m stock_predictor.ingestion status
python -m stock_predictor.ingestion inspect --ticker AAPL --interval 1d
python -m stock_predictor.ingestion validate --ticker AAPL --interval 1d
python -m stock_predictor.ingestion enable --ticker AAPL --no-enabled
python -m stock_predictor.ingestion enable --ticker AAPL
```

`--start` inclusivo, `--end` exclusivo. Fecha sin hora significa medianoche **UTC**.
Para límites de sesión precisos usar timestamps con offset, por ejemplo
`--start 2025-01-02T00:00:00-05:00 --end 2025-01-03T00:00:00-05:00`.
Sin fechas se intentan cinco años diarios y ventanas intradiarias dentro de la retención
conocida del proveedor. No se generan velas sintéticas ni se promete disponibilidad.

Cada bloque tiene un registro de ejecución confirmado antes de descargar. La escritura de
velas, eventos, diagnósticos, cobertura y cursor se confirma en una única transacción. Un
error hace rollback y se registra posteriormente como ejecución fallida. Si el proceso es
interrumpido bruscamente, el registro puede quedar `running`; reejecutar reanuda bloques no
confirmados. No se interpreta `MAX(timestamp)` como garantía de cobertura completa.

Los bloques con huecos, datos inválidos o velas provisionales se conservan como `partial` y
solo pueden confirmar el prefijo cerrado y sin huecos anterior al primer problema. Nunca
avanzan más allá de la primera vela ausente o provisional. Los huecos pueden corresponder a ausencia de operaciones:
son diagnósticos, no se interpolan y **no se declaran automáticamente descargados**. Esta
política conservadora puede provocar redescargas, especialmente con IEX.

`update` reconsulta la ventana de solapamiento y luego los bloques nuevos. Los UPSERT son
idempotentes; los cambios OHLCV/completitud se auditan en `data_revisions`. `--force` en
`backfill` reconsulta datos existentes para una reconciliación explícita:

```bash
python -m stock_predictor.ingestion backfill --ticker AAPL --interval 1d --force --start 2020-01-01 --end 2026-10-01
```

Tras un split o cambio retrospectivo de ajustes, puede ser necesario reconciliar **toda** la
historia ajustada, no solo siete días. No hay detección automática de revisiones fuera del solapamiento.

Códigos de salida: `0` correcto; `2` bloques parciales/fallidos o argumentos inválidos; `1`
error fatal de configuración, registro, conexión o proveedor. Los mensajes de excepción en
CLI se sanitizan para evitar secretos; las causas se clasifican por tipo. No se reintentan
errores permanentes de autenticación, autorización o solicitud. Timeouts de proveedor: 30 s.

## Yahoo Finance: ajustes y límites

Yahoo no requiere credenciales; es un servicio de disponibilidad variable, sin SLA. Un 429
puede impedir incluso consultar metadatos. El calendario utiliza `exchange-calendars` XNYS.

| Serie | Tratamiento |
|---|---|
| `split` | `auto_adjust=False`: precios históricos Yahoo, que reflejan splits; no garantía de precios originales negociados |
| `split_dividend` | `auto_adjust=True`: OHLC ajustados por splits/dividendos |
| `raw` | Rechazado explícitamente para evitar etiquetar incorrectamente datos Yahoo |

Retenciones **orientativas**, susceptibles a cambios: 1m 7 días, 5m/15m 60 días, 60m 730 días;
la cobertura diaria es desconocida. Las solicitudes se fragmentan y se respeta cualquier error
real del proveedor; no se toma una retención orientativa como evidencia de datos existentes.
Dividendo y split se descargan en los bloques diarios; no se conocen factores independientes
si el proveedor no los expone. No se define una serie de retorno total.

## Alpaca Market Data

```env
MARKET_DATA_PROVIDER=alpaca
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_DATA_FEED=iex
```

```bash
python -m stock_predictor.ingestion backfill --provider alpaca --ticker AAPL --interval 1d --feed iex --adjustment raw
python -m stock_predictor.ingestion inspect --ticker AAPL --provider alpaca --feed iex --adjustment raw
```

IEX y SIP se almacenan como feeds separados; IEX no representa todo el mercado. Se admiten
`raw`, `split`, `dividend` y `split_dividend` (`all` en Alpaca). La disponibilidad histórica y
de eventos depende de la cuenta y se declara desconocida cuando no está garantizada.
`alpaca-py` sigue los tokens de paginación; no se fija un límite total que trunque la descarga.
Se prueban dos páginas reales del flujo SDK mediante respuestas controladas.

Las velas Alpaca de 60m regulares se derivan desde minutos anclados a la apertura para
no mezclar premarket y sesión regular en velas horarias alineadas al reloj. Una vela derivada
con constituyentes ausentes no se emite. Su procedencia queda identificada.
No se ofrece 60m extendida derivada ni vela diaria de sesión extendida.
Eventos: dividendos en efectivo y splits forward/reverse del endpoint de corporate actions;
otros eventos se omiten explícitamente. Si no hay permisos para eventos, la carga diaria
falla de forma explícita; no se presenta una ingesta completa sin esas respuestas.
Metadatos Alpaca del endpoint de trading no se consultan para evitar requerir permisos
operativos adicionales. El identificador externo puede vincularse manualmente.

## Interactive Brokers (IBKR)

IBKR se integra mediante la API de TWS (`ibapi`) y requiere tener abierto TWS o IB Gateway
con las conexiones API habilitadas. El adaptador solo descarga datos históricos; no envía
órdenes ni consulta la cuenta.

```env
MARKET_DATA_PROVIDER=ibkr
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=10
IBKR_TIMEOUT_SECONDS=45
IBKR_EXCHANGE=SMART
IBKR_CURRENCY=USD
```

El feed del proyecto es `smart` y el ajuste admitido es `raw`:

```bash
python -m stock_predictor.ingestion backfill \
  --provider ibkr --ticker AAPL --interval 1d \
  --feed smart --adjustment raw \
  --start 2026-10-01 --end 2026-10-03
```

Para una serie intradiaria, por ejemplo de 1 minuto:

```bash
python -m stock_predictor.ingestion backfill \
  --provider ibkr --ticker AAPL --interval 1m \
  --feed smart --adjustment raw \
  --start 2026-10-01 --end 2026-10-03
```

El contrato se construye como acción estadounidense (`STK`) con `SMART` y `USD`; para
otros mercados o instrumentos se deberá ampliar la configuración de contrato. IBKR exige
suscripciones/permisos de datos y aplica límites de duración, tamaño de barra y frecuencia;
el servicio fragmenta las solicitudes en bloques conservadores. [Historical bars de IBKR](https://ibkrcampus.com/docs/tws-api/doc/market-data-historical/historical-bars/requesting-historical-bars)
documenta `reqHistoricalData`, `useRTH` y los tamaños de barra. Los límites de pacing y
permisos deben respetarse según la cuenta. [Pacing limitations](https://ibkrcampus.com/docs/web-api/trading/usage-and-availability/pacing-limitations)
.

IBKR no está integrado todavía con eventos corporativos ni metadatos de instrumentos en
este adaptador. Las acciones corporativas pueden mapearse explícitamente desde otra fuente
si se necesitan para ajustes.

## Identidad de instrumentos y cambio de proveedor

El UUID interno es estable. Ticker no es clave primaria ni identificador permanente.
`instrument_identifiers` conserva relaciones activas/inactivas, proveedor, símbolo e ID externo.
La fecha inicial indica cuándo se registró la relación, no una historia bursátil inferida.

No se infiere automáticamente que dos tickers de proveedores diferentes representan el
mismo instrumento. Tras verificar la identidad, se puede vincular explícitamente Alpaca al
UUID existente mostrado por `status`, **antes** de su primera carga:

```bash
python -m stock_predictor.ingestion map-identifier --instrument-id UUID_EXISTENTE --provider alpaca --symbol AAPL --external-id ID_SI_SE_CONOCE
```

Si no se vincula, se crea otro instrumento interno; no se mezclan historias. Relaciones
erróneas, cambios de ticker o reutilización requieren intervención explícita mediante
`bind_identifier`/`retire_identifier`; no hay reconciliación automática de fusiones o delistings.
La unicidad del identificador activo se asegura con un índice parcial PostgreSQL/SQLite.
Conservar datos de instrumentos excluidos del CSV es siempre el comportamiento predeterminado.

## Esquema y rendimiento

Tablas: `instruments`, `instrument_identifiers`, `data_providers`, `provider_feeds`,
`market_data`, `corporate_actions`, `ingestion_runs`, `ingestion_state`, `data_quality_issues`,
`ingestion_coverage` y `data_revisions`.

Para consultar el modelo visual y el detalle de claves y relaciones, ver el [DER del esquema](docs/DER.md).

La vista `public.vw_market_data` expone `ticker`, `session_date`, el campo calculado
`session_time`, OHLC, volumen y completitud para consultas compactas desde PostgREST o SQL.
El nombre correcto del catálogo es `instruments.ticker`.

Identidad de vela: instrumento + proveedor + feed + intervalo + ajuste + sesión + timestamp.
Todas las dimensiones son no nulas. Feeds y ejecuciones poseen integridad referencial.
Precios `NUMERIC(24,10)`, volumen `BIGINT`, fechas/timestamps UTC y JSON de procedencia.
`session_time` es un campo calculado de PostgREST a partir de `timestamp`; no se almacena
físicamente. Su valor se convierte según la zona horaria de la sesión de PostgreSQL.
`NUMERIC` conserva precisión decimal; ocupa más espacio que `DOUBLE PRECISION`. SQLite
usa conversión decimal y puede perder precisión, por lo que solo demuestra separación y
contratos; no certifica equivalencia numérica con PostgreSQL.

Bulk UPSERT, lotes limitados por parámetros, consultas keyset por timestamp, índices de
serie/tiempo y de instrumento/tiempo. Auditoría obtiene valores anteriores por lote.
PostgreSQL usa un advisory lock transaccional por serie en escrituras para serializar revisiones
concurrentes. Para procesos programados completos se recomienda `flock` (ver abajo).
No se han realizado benchmarks de decenas de millones de velas; hay diseño para lotes
acotados, no una certificación de rendimiento. Planes de consulta deben medirse en datos reales:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM market_data
WHERE instrument_id = 'UUID_EXISTENTE'
  AND timestamp >= '2025-01-01' AND timestamp < '2026-01-01';
```

Sin particionado inicial ni TimescaleDB. Evaluar particiones temporales cuando volumen,
vacuum, tiempos de consulta o mantenimiento lo justifiquen; la PK y unicidad deben incluir
la clave de partición. No particionar automáticamente solo por una previsión de volumen.

## Consultas desde Python

```python
from datetime import datetime, UTC
from stock_predictor.settings import Settings
from stock_predictor.adapters.persistence import create_uow_factory
from stock_predictor.domain.models import Series
from stock_predictor.services.queries import QueryService
from stock_predictor.services.calendar import Calendar

factory = create_uow_factory(Settings())
with factory() as uow:
    instrument = uow.repositories.resolve('AAPL', 'yahoo')
    if instrument is None:
        raise ValueError('Primero cargar AAPL')
series = Series(instrument.id, 'yahoo', 'default', '1d', 'split', 'regular')
query = QueryService(factory)
start, end = datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
frame = query.dataframe(series, start, end)
last = query.latest(series, start, end)
actions = query.actions(instrument.id, 'yahoo')
gaps = query.potential_gaps(series, start, end, Calendar())
with factory() as uow:
    unconfirmed = uow.repositories.get_missing_ranges(series, start, end)
```

`bars()` devuelve registros de dominio ordenados, paginados. `dataframe()` es una función
explícita en memoria; OHLC son strings decimales para preservar precisión y timestamps Pandas
son UTC. Convertir a float solo conscientemente para modelos numéricos.
`potential_gaps()` compara con el calendario; `get_missing_ranges()` informa cobertura de
ingesta aún no confirmada, que no es lo mismo que velas sin operaciones.

`compare(left, right, start, end)` hace un merge streaming de timestamps superpuestos y
muestra diferencias de cierre/volumen. Requiere mismo instrumento, intervalo, sesión y ajuste;
proveedor/feed quedan identificados. No combina las fuentes en una serie sintética.
Para selección de fuentes, elegir explícitamente un objeto `Series`; políticas más complejas
pueden implementarse sobre estas consultas, sin alterar el servicio de ingesta.

`aggregate(bars, '5m', Calendar())` crea velas bajo demanda a partir de una serie menor
regular, ordenada y completa. Se ancla a la apertura, admite última vela corta en cierres
anticipados y rechaza mezcla de series. No se persisten derivados por defecto.

## Exportación

```bash
python -m stock_predictor.ingestion export --ticker AAPL --format parquet --output aapl.parquet
python -m stock_predictor.ingestion export --ticker AAPL --format csv --output aapl.csv
```

CSV y Parquet se escriben por lotes; cada fila conserva todas las dimensiones de serie,
completitud y procedencia. OHLC decimal textual, timestamp ISO 8601 UTC, fecha ISO y
procedencia JSON. Archivos vacíos mantienen el esquema. La exportación no asegura snapshot
consistente frente a escrituras concurrentes; ejecutar fuera de una ingesta cuando se requiere
reproducibilidad y conservar la fecha, proveedor y rango del export.

## Pruebas

```bash
python -m pytest -q
```

Sin PostgreSQL, se ejecutan pruebas unitarias y contratos SQLite y se **saltan explícitamente**
los contratos PostgreSQL. Para ejecutar exactamente el mismo contrato en PostgreSQL real:

```bash
TEST_DATABASE_URL='postgresql+psycopg://stock_predictor:TU_PASSWORD@localhost:5432/stock_predictor_test' python -m pytest -q
```

Crear `stock_predictor_test` previamente. Los tests crean un schema con UUID y lo eliminan;
requieren permiso `CREATE` en la base. No usar una base productiva para las pruebas.
Los tests de proveedores usan mocks y paginación del SDK; no requieren claves ni inventan
datos para la base final. Los fixtures artificiales son exclusivamente datos de pruebas.

## Automatización local

Ejemplos en `deploy/`: cron y systemd user timer. Editar rutas antes de instalar. Se programa
una ejecución periódica corta, no un daemon, y se utiliza `flock` para evitar ejecuciones
solapadas. Los feriados no requieren condicionales de shell: el calendario maneja sesiones.
El horario cron depende de la zona horaria de tu máquina; Buenos Aires y Nueva York cambian
su diferencia relativa por DST. El timer es horario para actualizar también velas provisionales.
No se instala automáticamente ninguna tarea en tu equipo.

## Backup y restauración

```bash
pg_dump -h localhost -U stock_predictor -Fc stock_predictor -f stock_predictor.dump
createdb -h localhost -U stock_predictor stock_predictor_restore
pg_restore -h localhost -U stock_predictor -d stock_predictor_restore stock_predictor.dump
```

Usar prompt o `.pgpass` con permisos 600; no incluir contraseñas en el comando. Verificar
restauración en una base separada antes de cualquier reemplazo. Respaldar también `.env`
de forma privada, CSV y versión del código.

## Incorporar un proveedor

Implementar el puerto `MarketDataProvider`: capacidades explícitas, históricos con rango
semiabierto, eventos, metadatos y errores tipados. Adaptar símbolo y respuestas dentro de
su adaptador. Registrar una fábrica en `REGISTRY`; el servicio no cambia. Declarar
operaciones no soportadas, retención desconocida con `None` y políticas de ajuste reales.
Reutilizar fixtures de contrato de normalización y añadir pruebas específicas del SDK.

## Incorporar otra base

Implementar los puertos y una UoW que confirme todos los cambios relacionados de forma
atómica. Añadir la fábrica de ese backend en el punto de composición y reutilizar contratos.
MySQL precisa otro UPSERT, colaciones y alternativa para índices parciales. DuckDB requiere
verificar transacciones, concurrencia, claves y tipos. Las migraciones Alembic actuales son
para PostgreSQL/SQLite, no garantía de otros dialectos. El dominio, proveedores, validación,
ingesta y queries no necesitan conocer los cambios del motor.

Migrar implica crear esquema destino, exportar/importar tablas respetando claves, UUIDs,
UTC, precisión y secuencias de dependencias; validar conteos/valores y cambiar configuración.
Exportar solo OHLCV no reemplaza un traslado completo de identificadores, eventos y auditoría.
No hay promesa de migración automática.

## Administración de ejecuciones

La migración `0002` crea dos rutinas PostgreSQL:

```sql
-- Lista las ejecuciones, cantidad de velas y fecha de inicio.
SELECT * FROM public.list_ingestion_runs();

-- Elimina una ejecución y sus datos asociados.
CALL public.delete_ingestion_run('RUN_ID');

-- Elimina todos los datos de ingesta, sin parámetros.
CALL public.delete_all_run();
```

`delete_ingestion_run` elimina registros relacionados en `market_data`,
`corporate_actions`, `data_quality_issues` y `data_revisions`. Como
`ingestion_coverage` no conserva el `run_id`, también invalida los rangos
superpuestos de la misma serie. El cursor de `ingestion_state` solo se elimina
si no fue avanzado por una ejecución posterior. Ejecutar el `CALL` dentro de
una transacción permite revisarlo antes de confirmar:

```sql
BEGIN;
CALL public.delete_ingestion_run('RUN_ID');
-- Verificar resultados y luego usar COMMIT o ROLLBACK.
```

`delete_all_run()` elimina ejecuciones, velas, revisiones, problemas de calidad,
cobertura, estado y acciones corporativas. Conserva los catálogos de instrumentos
y proveedores.

## Fuentes técnicas y limitaciones conocidas

- [yfinance: documentación](https://ranaroussi.github.io/yfinance/reference/yfinance.functions.html)
- [yfinance: implementación de históricos](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)
- [Alpaca: solicitudes de barras](https://alpaca.markets/sdks/python/api_reference/data/stock/requests.html)
- [Alpaca: corporate actions](https://alpaca.markets/sdks/python/api_reference/data/corporate_actions/historical.html)
- [Alpaca: paginación y feeds](https://docs.alpaca.markets/us/reference/stockbarsingle-1)

La implementación usa XNYS para acciones estadounidenses convencionales. Instrumentos
con otro calendario, mercados especiales, suspensiones y cambios corporativos complejos
requieren ampliar la política de calendario/identidad. Horario extendido: 04:00–20:00 NY,
solo días de sesión; su final efectivo y disponibilidad pueden variar por proveedor. No se
infieren automáticamente huecos de sesión extendida. El calendario viene de una biblioteca
mantenida, no de consultas en tiempo real a una bolsa.

No se ha validado acceso real a Alpaca sin credenciales ni comportamiento de todos los planes.
No se almacenan correcciones de eventos eliminados por un proveedor si no devuelve una
marca explícita de eliminación. Auditoría registra cambios de eventos recibidos, no elimina
históricos por ausencia en una respuesta. Errores de base no tienen reintentos automáticos
para evitar repetir transacciones de resultado incierto; la siguiente ejecución reanuda con
idempotencia. Límites de Yahoo y permisos de Alpaca pueden cambiar.

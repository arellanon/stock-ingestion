# DER — Stock Ingestion

Documento de entidad-relación del esquema PostgreSQL de `stock-ingestion`.

Exportación gráfica: [DER.svg](./DER.svg).

Fuente de verdad: [`docs/postgres-schema.sql`](./postgres-schema.sql), generado a partir del esquema SQLAlchemy y la migración `0001`. El diagrama describe las 11 tablas funcionales del dominio. `alembic_version` se documenta aparte porque es metadata interna de Alembic.

## Diagrama entidad-relación

```mermaid
erDiagram
    data_providers ||--o{ provider_feeds : "ofrece"
    data_providers ||--o{ instrument_identifiers : "identifica"
    instruments ||--o{ instrument_identifiers : "tiene"

    instruments ||--o{ ingestion_runs : "se ingesta"
    provider_feeds ||--o{ ingestion_runs : "ejecuta"

    instruments ||--o{ ingestion_state : "mantiene cursor"
    provider_feeds ||--o{ ingestion_state : "define serie"

    instruments ||--o{ ingestion_coverage : "cubre"
    provider_feeds ||--o{ ingestion_coverage : "define serie"

    instruments ||--o{ market_data : "contiene velas"
    provider_feeds ||--o{ market_data : "publica"
    ingestion_runs ||--o{ market_data : "origina"

    instruments ||--o{ corporate_actions : "registra eventos"
    data_providers ||--o{ corporate_actions : "informa"
    ingestion_runs ||--o{ corporate_actions : "origina"

    instruments ||--o{ data_quality_issues : "presenta"
    provider_feeds ||--o{ data_quality_issues : "contextualiza"
    ingestion_runs ||--o{ data_quality_issues : "detecta"

    ingestion_runs ||--o{ data_revisions : "registra cambios"

    data_providers {
        varchar_32 name PK
    }

    provider_feeds {
        varchar_32 provider PK, FK
        varchar_32 name PK
    }

    instruments {
        varchar_36 id PK
        varchar_16 ticker
        boolean enabled
        json metadata
    }

    instrument_identifiers {
        varchar_36 id PK
        varchar_36 instrument_id FK
        varchar_32 provider FK
        varchar_32 symbol
        varchar_128 external_id
        date valid_from
        date valid_to
    }

    ingestion_runs {
        varchar_36 id PK
        varchar_36 instrument_id FK
        varchar_32 provider FK
        varchar_32 feed FK
        varchar_8 interval
        varchar_32 adjustment
        varchar_16 session
        timestamptz start
        timestamptz end
        varchar_16 status
        integer count
    }

    ingestion_state {
        varchar_36 instrument_id PK, FK
        varchar_32 provider PK, FK
        varchar_32 feed PK, FK
        varchar_8 interval PK
        varchar_32 adjustment PK
        varchar_16 session PK
        timestamptz cursor
    }

    ingestion_coverage {
        varchar_36 instrument_id PK, FK
        varchar_32 provider PK, FK
        varchar_32 feed PK, FK
        varchar_8 interval PK
        varchar_32 adjustment PK
        varchar_16 session PK
        timestamptz start PK
        timestamptz end PK
    }

    market_data {
        varchar_36 instrument_id PK, FK
        varchar_32 provider PK, FK
        varchar_32 feed PK, FK
        varchar_8 interval PK
        varchar_32 adjustment PK
        varchar_16 session PK
        timestamptz timestamp PK
        date session_date
        numeric_24_10 open
        numeric_24_10 high
        numeric_24_10 low
        numeric_24_10 close
        bigint volume
        boolean complete
        varchar_36 run_id FK
    }

    corporate_actions {
        varchar_36 instrument_id PK, FK
        varchar_32 provider PK, FK
        varchar_32 kind PK
        date ex_date PK
        varchar_128 external_id PK
        numeric_24_10 value
        varchar_36 run_id FK
    }

    data_quality_issues {
        varchar_36 id PK
        varchar_36 instrument_id FK
        varchar_32 provider FK
        varchar_32 feed FK
        varchar_8 interval
        varchar_32 adjustment
        varchar_16 session
        varchar_36 run_id FK
        varchar_32 code
        timestamptz timestamp
    }

    data_revisions {
        varchar_36 id PK
        varchar_32 entity
        json identity
        json previous
        json current
        varchar_36 run_id FK
        timestamptz created_at
    }
```

`provider_feeds` se relaciona con las tablas de series mediante la clave compuesta `(provider, feed)`. En el diagrama se muestra como una relación con `provider_feeds`; las columnas `interval`, `adjustment` y `session` completan la identidad lógica de cada serie, pero no son claves foráneas independientes.

## Tablas y propósito

| Tabla | Propósito | Clave primaria |
| --- | --- | --- |
| `data_providers` | Catálogo de proveedores externos (`yahoo`, `alpaca`, etc.). | `name` |
| `provider_feeds` | Feeds disponibles por proveedor (`iex`, `sip`, etc.). | `(provider, name)` |
| `instruments` | Instrumentos internos administrados por el sistema. | `id` |
| `instrument_identifiers` | Mapeo temporal entre un instrumento interno y el símbolo/ID de un proveedor. | `id` |
| `ingestion_runs` | Ejecuciones o bloques de ingesta, con rango solicitado, estado y resultado. | `id` |
| `ingestion_state` | Cursor incremental por serie. | `(instrument_id, provider, feed, interval, adjustment, session)` |
| `ingestion_coverage` | Rangos de tiempo confirmados por serie. | Dimensiones de serie + `(start, end)` |
| `market_data` | Velas OHLCV y su procedencia. | Dimensiones de serie + `timestamp` |
| `corporate_actions` | Eventos corporativos, como dividendos o splits. | `(instrument_id, provider, kind, ex_date, external_id)` |
| `data_quality_issues` | Diagnósticos de huecos, datos inválidos o velas provisionales. | `id` |
| `data_revisions` | Auditoría de valores anteriores y actuales ante correcciones. | `id` |

## Claves y reglas relevantes

- Todas las columnas de dimensión (`instrument_id`, `provider`, `feed`, `interval`, `adjustment`, `session`) son obligatorias en las tablas de series.
- `market_data` identifica una vela por instrumento, proveedor, feed, intervalo, ajuste, sesión y timestamp.
- `provider_feeds` garantiza que el feed pertenece al proveedor; las tablas de series reutilizan esa integridad mediante la FK compuesta `(provider, feed)`.
- `instrument_identifiers` tiene un índice único parcial para impedir dos identificadores activos con el mismo `(provider, symbol)` (`valid_to IS NULL`).
- `market_data.volume` no puede ser negativo. Además, `low > 0`, `high >= low` y los precios de apertura/cierre deben estar dentro del rango `[low, high]`.
- `market_data`, `corporate_actions`, `data_quality_issues` y `data_revisions` conservan referencia a `ingestion_runs` para trazabilidad.
- Los campos JSON (`metadata`, `provenance`, `identity`, `previous`, `current`) almacenan información flexible del instrumento, procedencia y auditoría.

## Metadata de migraciones

`alembic_version(version_num)` es una tabla técnica de Alembic. Guarda la revisión aplicada (`0001`) y no participa del modelo de negocio ni tiene relaciones con las tablas funcionales.

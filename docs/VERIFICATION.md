# Verificación — 13 de octubre de 2026

## Ejecutado en este entorno

- Python 3.12: instalación editable y construcción de wheel sin dependencias.
- Suite final: **27 pruebas aprobadas, 15 saltadas**; una advertencia de deprecación transitiva de `websockets`.
- Todos los comandos CLI responden a `--help`.
- Migración inicial Alembic: upgrade y downgrade reales en SQLite.
- Generación offline de DDL PostgreSQL con Alembic; véase `postgres-schema.sql`.
- Compilación Python del código, migraciones y pruebas.
- Contratos SQLite: UPSERT, unicidad, rollback de velas/estado, FK, auditoría,
  cobertura, habilitación, identidad externa explícita y reutilización de ticker.
- Servicios: idempotencia, export CSV/Parquet por lotes, reanudación, errores,
  velas inválidas/duplicadas, prefijos cerrados, comparación entre fuentes,
  horario de verano, feriados y cierre anticipado.
- Proveedores con respuestas controladas: normalización Yahoo/Alpaca,
  dividendos/splits, selección y errores, paginación de dos páginas del SDK
  y derivación Alpaca 60m anclada a la apertura.

## Integraciones pendientes de verificación real

| Integración | Resultado y motivo |
|---|---|
| Yahoo Finance | Intento real AAPL diario: HTTP 429 durante obtención de metadatos; no se almacenó ni inventó un histórico real. CLI terminó con error temporal. |
| PostgreSQL | Servidor no disponible. Las 15 variantes PostgreSQL de contratos/servicios se saltaron por ausencia de `TEST_DATABASE_URL`. DDL compilado no equivale a integración ejecutada. |
| Alpaca | Sin credenciales suministradas. Adaptador y SDK probados con mocks, sin afirmar acceso real o disponibilidad de feeds/eventos. |
| Escala de millones de velas | No se realizó benchmark ni EXPLAIN sobre una base cargada. Arquitectura por lotes, no certificación de capacidad medida. |

Para cerrar las verificaciones: instalar PostgreSQL siguiendo README, ejecutar contratos con
`TEST_DATABASE_URL`, realizar una carga Yahoo pequeña cuando cese el rate limit y probar
Alpaca con credenciales/entitlements propios. Probar backup/restauración antes de usar en producción.

## Alcance y límites explícitos

Incluye los dos adaptadores, puertos, persistencia PostgreSQL/SQLite, transacciones,
CLI, consultas, eventos, auditoría, validación, agregación, export, migraciones y documentación.
No es una certificación de funcionamiento de APIs o motores no ejecutados.
Identidad corporativa compleja es explícita/manual. MySQL/DuckDB requieren un adaptador nuevo.
Horario extendido y ajustes se describen en README. No se interpretan datos no disponibles
como cotizaciones faltantes que deban rellenarse. Fuera de la ventana incremental se requiere
reconciliación `--force`. No hay combinación automática de fuentes ni métricas de predicción.

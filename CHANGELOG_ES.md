# Registro de cambios — Anki JSON Protocol

## v0.7.0 — Cambios estructurales versionados

- Se añade un esquema de plan de cambio estructural y un esquema de informe.
- Se compara la definición fuente de un tipo de nota (campos, orden, plantillas y CSS) con la definición destino.
- El preflight en vivo es de solo lectura, verifica el perfil `AJP-SANDBOX` y rechaza cualquier diferencia inesperada en el contrato de origen.
- La aplicación crea una versión paralela del tipo y copia solo las notas sintéticas declaradas; no edita ni elimina la fuente.
- Se añaden pruebas simuladas para cambios estructurales, precondiciones y barreras de escritura.
- Las 53 pruebas automatizadas locales pasaron con `python -m pytest -q` (`53 passed in 1.95s`; 2026-10-28).
- La prueba estructural en vivo pasó el 2026-10-28 en el perfil desechable `AJP-SANDBOX`: se creó o reutilizó el tipo paralelo `@1.2.0`, se verificaron el campo `Difficulty` y las plantillas `Forward`/`Reverse`, se generaron dos tarjetas destino, se preservó la nota y las tarjetas de origen y la segunda ejecución fue idempotente.

## v0.6.0 — Actualizaciones controladas de campos

- Se añade el protocolo independiente `anki-json-note-update-pack` v0.1.0.
- Se añade `scripts/apply_note_updates.py`, con planificación predeterminada de solo lectura.
- Las escrituras requieren confirmar explícitamente el perfil Anki activo.
- El actualizador solo opera en notas que coincidan con un `source_id` administrado por AJP, tipo de nota esperado y precondiciones de campos.
- Si existe cualquier conflicto en el preflight, no se escribe ningún campo del paquete.
- Se repiten las comprobaciones inmediatamente antes de escribir para reducir la posibilidad de sobrescribir un cambio concurrente.
- Repetir un paquete es idempotente cuando los campos ya tienen el valor de destino.
- El flujo de creación `content_pack.json` continúa siendo CREATE-ONLY; las migraciones siguen creando copias nuevas.
- Se agrega ejemplo de actualización para la nota sintética de migración en `AJP-SANDBOX` y ocho pruebas unitarias.
- Se ejecutaron 42 pruebas automatizadas y se validó el ejemplo con el esquema JSON en este entorno.

### Límites conocidos

- No se actualizan tipos de nota ni plantillas; los cambios estructurales siguen siendo migraciones versionadas separadas.
- AnkiConnect no tiene transacciones multi-nota; una falla durante una serie de escrituras puede dejar una aplicación parcial.
- Las plantillas condicionales pueden cambiar el conjunto de tarjetas generadas al actualizar campos. Debe comprobarse en un perfil de prueba antes de usar con tipos de nota con contenido condicional.
- La prueba en vivo de actualización aún necesita ejecutarse en la instancia local del usuario.

## Estado de pruebas integradas (informe aportado por el usuario, 2026-10-26)

- La prueba de integración básica en `AJP-SANDBOX` pasó: dos tarjetas, plantillas `Forward`/`Reverse`, segunda importación idempotente.
- La migración por copia pasó contra Anki real: creó el tipo y mazo destino, creó una copia con dos tarjetas, preservó el origen y reutilizó el destino en segunda ejecución.

## v0.5.0 — Migraciones por copia

- Se añade un ejecutor de migraciones que crea notas nuevas en el tipo de destino y conserva las originales.
- La ejecución de migraciones es idempotente y se protege con confirmación del perfil `AJP-SANDBOX`.
- Se agrega prueba de migración contra Anki real y pruebas automatizadas con servidor simulado.

## v0.4.1 — Corrección de la prueba de integración

- La prueba de tarjetas consulta `modelTemplates` para verificar el nombre y el HTML de las plantillas, en vez de depender de una propiedad `template` que `cardsInfo` no proporciona.

## v0.4.0 — Prueba de integración y planificación de migraciones

- Se añade la prueba de integración con guardia de perfil y el primer formato de plan de migración.

## v0.3.0 — Paquete declarativo y compatibilidad

- Se formaliza `content_pack.json` para mazos, tipos de nota, plantillas y notas.
- El generador sigue siendo CREATE-ONLY.

## v0.2.0 — Inventario estructural

- Se amplía la exportación de metadatos de estructura de Anki.

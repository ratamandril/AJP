# AJP v0.7.0 — cambios estructurales versionados

Esta extensión forma parte del kit completo AJP v0.7.0 junto con el código base v0.6.0. Añade archivos nuevos; no reemplaza los generadores ni ejecutores de migración existentes.

## Objetivo

Probar una modificación estructural sin editar el tipo que ya está en uso. El ejemplo sintético parte de `AJP-SANDBOX ...@1.1.0` y propone crear `AJP-SANDBOX ...@1.2.0` con:

- Campo nuevo `Difficulty`.
- Cambios en las plantillas `Forward` y `Reverse` para presentar `Difficulty` junto con `Context`.
- Ajuste del CSS para el campo nuevo.
- Copia de una sola nota sintética a `AJP-SANDBOX::Structural Test`.

Los archivos/inventarios anteriores se consideran pruebas, no contenido oficial.

## Archivos añadidos

- `schemas/structural_change_plan.schema.json`: esquema del plan estructural.
- `schemas/structural_change_report.schema.json`: esquema del informe de comparación.
- `examples/structural_change_plan.live_smoke.json`: especificación para el entorno de prueba.
- `scripts/structural_change_core.py`: validación, huellas estructurales y comparación.
- `scripts/plan_type_change.py`: preflight vivo de solo lectura.
- `scripts/apply_structural_change.py`: crea la versión paralela usando el ejecutor de migración existente, pero solo después de verificar la definición exacta de origen.
- `scripts/run_live_structural_change_test.py`: verifica las tarjetas destino, la conservación del original y la idempotencia.
- `tests/test_structural_change.py`, `tests/test_structural_change_guard.py`: pruebas simuladas.

## Preparación

Estos archivos ya están integrados en el archivo fuente completo `AJP_v0.7.0_source.zip`. Extrae el paquete y ejecuta desde la raíz:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

La implementación reutiliza los componentes de AJP para migración por copia (`scripts/apply_migration.py`, `scripts/plan_migration.py`, `scripts/ajp_core.py`) y el formato de plan de migración v0.2.0.

## Paso 1: comparar en vivo sin escribir

Con Anki abierto en el perfil `AJP-SANDBOX`:

```powershell
python scripts\plan_type_change.py examples\structural_change_plan.live_smoke.json --out reports\structural_change_plan.json
```

El plan comprueba el perfil activo y que la definición real de `@1.1.0` coincida exactamente con `source.expected_definition`. Si cambió el modelo de origen, se detiene; no modifica ni crea objetos. El informe señala campos añadidos/retirados, orden, plantillas cambiadas, CSS, tarjetas actuales y el destino propuesto.

El resultado esperado para este ejemplo es:

- Campo añadido: `Difficulty`.
- Campos retirados: ninguno.
- Plantillas cambiadas: `Forward` y `Reverse`, ambas en el lado de respuesta.
- CSS: cambiado.
- Plantillas destino: 2.
- Mazo destino: se crearía si falta.
- Notas destino: una copia nueva; el original queda intacto.

## Paso 2: prueba estructural real

Después de revisar el informe, ejecuta solo en el perfil desechable `AJP-SANDBOX`:

```powershell
python scripts\run_live_structural_change_test.py --confirm-write AJP-SANDBOX --out reports\live_structural_change_test.json
```

La prueba crea/reutiliza la versión `@1.2.0`, verifica los campos y el HTML renderizado, comprueba que existan dos tarjetas, compara los campos/etiquetas/tarjetas de origen antes y después, y repite la operación para confirmar idempotencia.

La prueba no borra objetos al terminar. No la ejecutes en un perfil con notas que quieras conservar.

## Reglas

- No se modifica el modelo fuente in-place; se crea una versión paralela.
- La definición de origen debe coincidir con el contrato exacto del plan. Un cambio no explicado detiene el proceso.
- Solo se seleccionan `source_id` explícitos; no se admiten comodines.
- Las notas originales, sus etiquetas y sus tarjetas se comprueban tras la migración.
- La programación e historial de repaso no se copian a las notas nuevas.
- AnkiConnect no proporciona una transacción atómica multiobjeto. Un fallo durante las escrituras puede dejar la estructura destino parcialmente creada; el proceso no hace rollback destructivo y debe reejecutarse después de revisar el informe.
- El número real de tarjetas se verifica después de crear. En tipos con reglas condicionales el número de tarjetas puede variar de la cantidad de plantillas definidas.

## Estado

Pruebas locales simuladas: 10 pasan. La comparación y migración estructural contra tu Anki real aún requiere ejecutar los comandos anteriores y compartir `reports\live_structural_change_test.json`.

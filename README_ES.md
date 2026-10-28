# Anki JSON Protocol (AJP) — kit de trabajo v0.7.0

**Versión del kit:** `0.7.0`
**Formato de paquetes de actualización de campos:** `0.1.0`  
**Formato de paquetes de contenido:** `0.2.0`  
**Formato de planes de migración:** `0.2.0`  
**Objetivo:** describir en JSON la estructura y el contenido que se quiere crear en Anki, y ejecutar operaciones explícitas mediante AnkiConnect.

## Estado y alcance

Los inventarios que se compartieron durante el desarrollo (`anki_structure.json` y `anki_full_snapshot.json`) se consideran **fixtures de prueba, no contenido oficial del usuario**. No se deben usar como fuente de verdad para contenido de estudio.

La prueba de integración básica y la prueba de migración por copia se ejecutaron en el perfil `AJP-SANDBOX` y devolvieron `passed`, según los informes generados localmente. El kit no puede conectarse al `localhost:8765` de otra computadora.

## Componentes

| Entidad | Función |
|---|---|
| `collection_snapshot.json` | Inventario exportado de lo que existe en Anki. |
| `content_pack.json` | Declaración de mazos, tipos de nota, campos, plantillas y notas que se desean crear. |
| `migration_plan.json` | Declaración explícita de cómo copiar notas de una versión de tipo de nota a otra. |
| `note_update_pack.json` | Cambios precondicionados en campos de notas existentes marcadas por AJP. |
| Exportador | Consulta AnkiConnect y produce un snapshot JSON. |
| Generador de contenido | Valida el paquete y crea solo objetos nuevos, reutilizando definiciones idénticas. |
| Ejecutor de migraciones | Por defecto hace un preflight sin escribir; la ejecución requiere confirmación y el perfil `AJP-SANDBOX`. |
| Actualizador de campos | Modifica solo campos declarados de notas AJP; requiere valores esperados, nombre de tipo exacto y confirmación del perfil activo. |
| Planificador de cambios estructurales | Compara campos, plantillas y CSS; propone una nueva versión paralela del tipo de nota y conserva la definición fuente. |

Una nota contiene valores de campos. El tipo de nota contiene plantillas y estas son las que hacen que Anki genere las tarjetas; las tarjetas no se declaran como registros independientes en `content_pack.json`.

## Requisitos

- Anki de escritorio abierto.
- AnkiConnect instalado y activo.
- Python 3.10 o posterior recomendado.
- `jsonschema` opcional para validar los esquemas formales mediante el validador.

Instala las dependencias:

```powershell
python -m pip install -r requirements.txt
```

## 1. Exportar el inventario

Inventario solo estructural (sin el texto de notas ni preguntas/respuestas renderizadas):

```powershell
python scripts\export_anki_snapshot.py --schema-only --no-tag-inventory --out anki_structure.json
```

Inventario con contenido, configuraciones de mazos y nombres multimedia:

```powershell
python scripts\export_anki_snapshot.py --out anki_full_snapshot.json --include-deck-configs --include-media-inventory
```

Revisa el JSON antes de compartirlo: el snapshot completo incluye el texto real de las notas y las preguntas y respuestas renderizadas.

## 2. Validar y crear un paquete de contenido

Validación local, sin conectarse a Anki:

```powershell
python scripts\validate_content_pack.py examples\content_pack.fisica.json
```

Planificar una creación sin escribir nada:

```powershell
python scripts\apply_anki_pack.py examples\content_pack.fisica.json --report-out reports\fisica_plan.json
```

Aplicar después de revisar el plan:

```powershell
python scripts\apply_anki_pack.py examples\content_pack.fisica.json --apply --report-out reports\fisica_apply.json
```

La política del generador de contenido es **CREATE-ONLY**: reutiliza mazos y tipos de nota compatibles, crea los que falten y se detiene ante conflictos. No actualiza ni elimina definiciones existentes. Este comando de contenido no incluye una barrera de perfil fija, así que úsalo en una colección de desarrollo hasta que se añada una política de despliegue apropiada.

## 3. Prueba de integración básica ya superada

La primera prueba real se ejecutó con AnkiConnect API 6 en el perfil desechable `AJP-SANDBOX` y devolvió `result: passed`. Comprobó el tipo de nota de prueba, las plantillas `Forward` y `Reverse`, dos tarjetas renderizadas y la idempotencia de una segunda importación.

Para repetirla:

```powershell
python scripts\run_live_smoke_test.py --confirm-write AJP-SANDBOX --out reports\live_smoke_test.json
```

No se eliminan los objetos de prueba al terminar. No ejecutes la prueba en un perfil que contenga notas que quieras conservar.

## 4. Migrar un tipo de nota: copia a una versión nueva

La migración nunca modifica ni elimina las notas originales. Crea un tipo destino versionado y copia en él solo los `source_id` declarados. Los nuevos objetos tienen programación de repaso nueva; no se transfiere el historial de repasos.

### 4.1 Revisar el plan sin modificar Anki

La validación estructural no se conecta a Anki:

```powershell
python scripts\plan_migration.py examples\migration_plan.live_smoke.json --out reports\migration_schema_check.json
```

El preflight real consulta Anki, comprueba que origen y notas seleccionadas existan, calcula el mapeo, detecta conflictos con la versión destino y **no escribe** sin `--apply`:

```powershell
python scripts\apply_migration.py examples\migration_plan.live_smoke.json --out reports\migration_plan.json
```

Lee el plan y confirma que el origen `AJP-SANDBOX - Smoke Basic ...@1.0.0`, la nota sintética `org.openai.ajp.smoke.note.001`, el tipo destino `...@1.1.0` y el mazo `AJP-SANDBOX::Migration Test` son los esperados.

### 4.2 Ejecutar prueba de migración en vivo

**Solo en el perfil desechable `AJP-SANDBOX`.** La orden siguiente crea la nueva versión del tipo de nota, el mazo destino y una nota migrada, comprueba las dos tarjetas, confirma que los campos/etiquetas/tarjetas de la nota original se conservaron y ejecuta la misma migración por segunda vez para probar la idempotencia.

```powershell
python scripts\run_live_migration_test.py --confirm-write AJP-SANDBOX --out reports\live_migration_test.json
```

También se puede aplicar el plan directamente —solo después de revisar su salida en modo plan-only—:

```powershell
python scripts\apply_migration.py examples\migration_plan.live_smoke.json --apply --confirm-write AJP-SANDBOX --out reports\migration_apply.json
```

Vuelve a ejecutar el mismo comando para comprobar que el destino coincidente se reutiliza y que no se crea una segunda copia.

### Qué hace el ejecutor de migraciones

- Exige `--confirm-write AJP-SANDBOX` y verifica el perfil activo antes de escribir.
- Selecciona solamente `source_id` explícitos; no admite comodines ni migrar todos los objetos de un tipo.
- Requiere que cada `source_id` identifique una única nota del tipo de origen declarado.
- Crea una versión destino con nombre diferente; si ese nombre existe con otra definición, aborta sin actualizarla.
- Permite mapear cada campo destino mediante `copy`, `constant` o `empty`.
- Genera un `source_id` de destino estable para esa nota/familia/versión, de modo que una segunda ejecución sea idempotente.
- Copia etiquetas de usuario, elimina del destino solo los marcadores internos `ajp_uid_*` heredados de la fuente y agrega una nueva identidad AJP y marcadores de procedencia.
- Nunca actualiza ni elimina las notas originales.
- Deja un informe JSON con notas creadas, reutilizadas, fallidas y comprobaciones de preservación.

**Limitación transaccional:** AnkiConnect no proporciona una transacción atómica que abarque la creación del mazo, tipo de nota y todas las notas. Un fallo de conexión durante las escrituras puede dejar parte de la estructura nueva creada. El ejecutor no hace rollback destructivo; revisa el informe y vuelve a ejecutar tras corregir el problema. Las identidades estables están diseñadas para permitir la reanudación idempotente.

## 5. Convención de versionado

Usa un identificador lógico estable y un nombre de modelo que incluya su versión, por ejemplo:

- Familia: `org.estudio.fisica.magnitud`
- Tipo Anki v1: `AJP::org.estudio.fisica.magnitud@1.0.0`
- Tipo Anki v2: `AJP::org.estudio.fisica.magnitud@1.1.0`

El `type_id` permanece estable entre versiones. El nombre de modelo se cambia para que Anki pueda conservar ambas definiciones en paralelo. La versión nueva debe ser mayor que la de origen. Los tipos de nota externos que no utilicen la convención no se consideran automáticamente gestionados por AJP.

## 6. Pruebas automatizadas

Estas pruebas usan datos y servidores simulados y no modifican tu Anki:

```powershell
python -m unittest discover -s tests -v
```

Se debe distinguir la validación local de JSON de la prueba de integración real. Solo el informe producido por `run_live_smoke_test.py` o `run_live_migration_test.py` confirma la ejecución contra el AnkiConnect local.

## Límites de esta versión

- El generador de contenido sigue siendo CREATE-ONLY; la actualización de campos se realiza mediante un paquete separado. No se actualizan tipos de nota ni plantillas existentes in situ; los cambios estructurales se hacen mediante una definición versionada paralela y migración por copia.
- El ejecutor de migraciones está deliberadamente limitado al perfil `AJP-SANDBOX`; no debe usarse para migraciones de producción.
- Las migraciones hacen copias y reinician la programación de repaso. No transfieren historial.
- No transporta bytes de multimedia como parte de un paquete de contenido.
- No clona toda opción avanzada de los tipos de nota ni todos los tipos especiales de complementos (por ejemplo, oclusión de imagen).
- Las notas de prueba anteriores no constituyen contenido de estudio oficial.

## Referencias

- AnkiConnect API: <https://github.com/ankitects/anki-connect>
- Manual de Anki — notas y generación de tarjetas: <https://docs.ankiweb.net/getting-started.html>
- Manual de Anki — plantillas: <https://docs.ankiweb.net/templates/intro.html>


## 5. Actualizar campos de notas existentes de forma controlada

La creación y las migraciones mantienen sus políticas anteriores. Para modificar el contenido de una nota ya gestionada por AJP se utiliza un paquete separado `anki-json-note-update-pack` v0.1.0. El paquete de ejemplo está dirigido únicamente a la nota sintética `Context` creada por la prueba de migración en `AJP-SANDBOX`.

Primero, simula el cambio sin escribir:

```powershell
python scripts\apply_note_updates.py examples\note_update_pack.live_smoke.json --report-out reports\note_update_plan.json
```

Lee `reports\note_update_plan.json`. El actualizador exige que la nota tenga el marcador correspondiente a `source_id`, que exista exactamente una coincidencia, que `expected_model_name` coincida y que cada campo a cambiar tenga una precondición en `expected_fields`. Si encuentra un conflicto, se detiene antes de escribir.

Para aplicar el cambio, solo en el perfil desechable `AJP-SANDBOX`:

```powershell
python scripts\apply_note_updates.py examples\note_update_pack.live_smoke.json --apply --confirm-profile AJP-SANDBOX --report-out reports\note_update_apply.json
```

Vuelve a ejecutar el comando de aplicación para comprobar la idempotencia: los campos ya en el valor de destino deben reportarse como `already_applied`, sin una segunda escritura. La herramienta no cambia tipo de nota, plantillas, mazo, etiquetas ni programación. Las plantillas condicionales pueden crear o dejar de generar tarjetas al cambiar campos, de modo que los casos de ese tipo deben probarse en una colección desechable. AnkiConnect no proporciona transacciones multi-nota; un fallo durante varios cambios puede dejar una aplicación parcial, que quedará registrada en el informe.

## 7. Cambios estructurales versionados (v0.7.0)

AJP puede comparar una definición de tipo de nota existente con una definición destino que añade o reordena campos, cambia plantillas o CSS. La operación se hace creando una nueva versión en paralelo, no modificando el tipo fuente. El plan verifica la definición fuente esperada antes de permitir cualquier escritura.

Primero genera un plan de solo lectura contra el perfil sintético `AJP-SANDBOX`:

```powershell
python scripts\plan_type_change.py examples\structural_change_plan.live_smoke.json --out reports\structural_change_plan.json
```

Revisa `reports\structural_change_plan.json`. Si coincide exactamente con lo esperado, la prueba en vivo de extremo a extremo se ejecuta con:

```powershell
python scripts\run_live_structural_change_test.py --confirm-write AJP-SANDBOX --out reports\live_structural_change_test.json
```

La prueba crea el tipo `@1.2.0` y copia la nota sintética a `AJP-SANDBOX::Structural Test`; verifica las plantillas, los campos, la preservación del original y la idempotencia. No ejecutes la escritura en otro perfil. **Esta prueba en vivo aún está pendiente de ejecución en la instalación local del usuario.**

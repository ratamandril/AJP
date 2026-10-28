# Anki JSON Protocol (AJP) — reglas de compatibilidad 0.2.0

Este documento define el comportamiento observable del paquete declarativo `content_pack.json` y del generador `apply_anki_pack.py`.

## 1. Modelo mental

- **Mazo (`deck`)**: destino de organización y estudio de las tarjetas.
- **Tipo de nota (`note_type`)**: estructura compartida que define los campos y las plantillas.
- **Campo (`field`)**: un valor almacenado dentro de cada nota.
- **Plantilla de tarjeta (`card_template`)**: HTML de pregunta y respuesta. Anki la usa para determinar qué tarjetas genera.
- **Nota (`note`)**: instancia de un tipo de nota con valores de campos y etiquetas.
- **Tarjeta (`card`)**: objeto que Anki genera desde una nota y una plantilla. AJP 0.2.0 no declara tarjetas individuales en el paquete.

Anki puede generar cero, una o varias tarjetas a partir de una nota dependiendo del tipo y sus plantillas. En los tipos de nota estándar, la generación se basa en los campos usados en la cara de la pregunta; los tipos Cloze tienen lógica especial. Ver el manual de Anki sobre generación de tarjetas.

## 2. Política de mutación: CREATE-ONLY

AJP 0.2.0 solo crea objetos que faltan y agrega notas nuevas. No modifica ni elimina mazos, tipos de nota, campos, plantillas, notas, tarjetas, etiquetas ya existentes o programación de repasos.

La ejecución predeterminada es de planificación y solo lectura. Para escribir hay que añadir `--apply` explícitamente.

### Mazos

- Si un mazo declarado ya existe con el mismo nombre, se reutiliza.
- Si no existe, se crea.
- No se cambia su configuración de estudio, límites diarios ni opciones de repaso.
- El paquete debe declarar cada mazo utilizado por sus notas.

### Tipos de nota

Cada definición incluye `type_id`, `version`, `name`, `mode`, `kind`, `fields`, `required_fields`, `card_templates` y `css`.

- `type_id`: identificador portable usado para referenciar el tipo dentro del protocolo y del paquete. **Anki no lo almacena como un ID nativo**.
- `template_id`: identificador portable de la plantilla dentro del protocolo; el cotejo con Anki se hace usando el nombre, orden y HTML de la plantilla. Anki no almacena el `template_id` de AJP.
- `version`: versión semántica de la definición lógica. Por sí sola no actualiza ni migra nada en Anki.
- `name`: nombre real con el que Anki identifica el tipo de nota en esta versión del protocolo.
- `mode: "ensure"`: si el nombre no existe, el generador crea el tipo; si ya existe, solo lo reutiliza si coincide con la firma administrada.
- `mode: "reuse"`: el tipo ya debe existir y coincidir con la firma administrada. Si falta, el paquete se rechaza.

La firma de compatibilidad de 0.2.0 compara: nombre, clase standard/Cloze, orden y nombres de campos, nombres y orden de plantillas, HTML de pregunta y respuesta, CSS y ausencia de sobrescrituras de mazo en plantillas.

Si un tipo de nota con el mismo nombre no coincide, se detiene el preflight antes de escribir. El generador no “arregla” ni actualiza el tipo; la solución es corregir el paquete o declarar un nombre/versionado diferente.

### Alcance exacto del tipo de nota

La API `createModel` de AnkiConnect expone los nombres ordenados de campos, CSS, standard/Cloze y el HTML de pregunta/respuesta de las plantillas. AJP 0.2.0 solo crea tipos con esas propiedades.

AJP 0.2.0 no pretende clonar propiedades avanzadas como fuente/tamaño/estilo por campo, descripciones de campo, plantillas de navegador, LaTeX pre/post o sobrescrituras de mazo de una plantilla. Estas propiedades no están incluidas en la firma de compatibilidad. Si necesitas una reproducción fiel de esas opciones, hay que ampliar el esquema y el adaptador de AnkiConnect antes de generar ese tipo en otra colección.

### Notas e idempotencia

Cada nota requiere un `source_id` estable.

- El generador calcula una etiqueta interna determinista `ajp_uid_<hash>` a partir de `source_id` y la adjunta al crear la nota.
- Si Anki ya tiene una nota con esa etiqueta, se omite la importación.
- Se le pide a Anki que rechace duplicados de acuerdo con su comprobación de primer campo en toda la colección (sin comparar entre tipos de nota distintos).
- Se rechazan `source_id` repetidos dentro del mismo paquete.
- Se rechazan duplicados del primer campo dentro del mismo tipo en un mismo paquete, porque AJP 0.2.0 no tiene una directiva para permitir duplicados intencionales.
- Los campos desconocidos son error. Los campos no requeridos que se omitan se envían a Anki como cadena vacía.
- El primer campo de Anki no puede quedar vacío.

Si una nota es omitida porque es duplicada, el paquete no actualiza su contenido. Las actualizaciones necesitarán un protocolo de cambios explícitos en una versión futura.

## 3. Comprobaciones antes de escribir

Antes de crear nada, el generador:

1. valida el JSON y las referencias internas;
2. comprueba que cada nota referencia un `type_id` declarado y un mazo declarado;
3. valida que cada plantilla refiera solo a campos del tipo de nota o a campos especiales reconocidos;
4. consulta AnkiConnect para comprobar los mazos y tipos de nota existentes;
5. compara toda definición de tipo de nota con el tipo de mismo nombre, si existe;
6. comprueba qué `source_id` ya fueron importados.

Solo después de que las comprobaciones de estructura y compatibilidad pasen comienza la fase de escritura.

## 4. Límite transaccional

AnkiConnect no ofrece una transacción atómica que abarque varios `createDeck`, `createModel` y `addNote`. Si Anki se cierra o falla una llamada a mitad de la fase `--apply`, pueden quedar creados algunos mazos/tipos de nota aunque no se hayan creado todas las notas. El informe JSON permite ver qué se alcanzó a crear. Al volver a ejecutar, el generador reutiliza los mazos/tipos compatibles y omite las notas ya marcadas por `source_id`.

No se ejecuta rollback automático: borrar los objetos creados podría borrar un objeto que el usuario haya comenzado a utilizar después de la interrupción.

## 5. Recursos multimedia

En 0.2.0 los valores de campos pueden contener referencias como `<img src="archivo.png">` o `[sound:archivo.mp3]`, pero el paquete no transporta los bytes de esos archivos ni comprueba que existan en el destino. Si el contenido usa multimedia, los archivos deben estar instalados en el directorio de medios de Anki, y una futura versión del protocolo deberá definir un bloque de recursos explícito.

## 6. No incluido en 0.2.0

- Actualizar notas existentes por `source_id`.
- Renombrar, modificar o migrar tipos de nota existentes.
- Eliminar objetos, vaciar tarjetas o editar programación de repasos.
- Crear o editar mazos filtrados o configuraciones de repaso.
- Sincronizar el historial completo de repasos.
- Empaquetar medios binarios.
- Reproducir completamente tipos avanzados como Oclusión de imagen u otras extensiones que requieran metadatos extra.

## Anexo v0.4.0 — alcance de las pruebas y migraciones

### Los snapshots de desarrollo no son un catálogo oficial

Los inventarios compartidos durante las pruebas se consideran fixtures. No son una declaración de qué contenido oficial debe conservarse o gestionarse. Para contenido nuevo, AJP solo considera gestionadas las notas que identifica con las etiquetas `ajp_uid_<hash>` derivadas de `source_id` y los tipos de nota cuya identidad/versionado se acordó explícitamente.

### Prueba real de integración

La única prueba que escribe contra Anki en esta versión es `scripts/run_live_smoke_test.py`. Exige que `getActiveProfile` devuelva exactamente `AJP-SANDBOX` y una confirmación explícita en CLI. Crea nombres con prefijo de prueba, genera dos tarjetas, vuelve a aplicar el mismo paquete y verifica que no se duplique la nota. No elimina los objetos: el usuario debe revisar y retirar el perfil desechable manualmente.

### Versionado de tipo de nota

Para tipos nuevos, `type_id` identifica la familia conceptual del tipo. `version` marca una definición específica. El nombre de Anki debe incluir un marcador de versión, por ejemplo `AJP::org.estudio.fisica.magnitud@1.0.0`. Un cambio estructural se propone como una nueva definición y un nombre de modelo distinto; el generador create-only no cambia el modelo previo.

### Planes de migración v0.2.0 y ejecutor de sandbox (kit v0.5.0)

`migration_plan.schema.json` define planes que seleccionan `source_id` explícitos y mapean todos los campos de destino mediante `copy`, `constant` o `empty`. `scripts/plan_migration.py` valida la estructura sin conectarse a Anki. `scripts/apply_migration.py` consulta AnkiConnect y realiza un preflight real; por defecto no escribe. La escritura requiere `--apply --confirm-write AJP-SANDBOX` y que el perfil activo sea exactamente `AJP-SANDBOX`.

La estrategia es `copy_to_new_notes`: crea una versión nueva del tipo y notas destino, conserva las notas originales y las tarjetas origen, y reinicia la programación de las tarjetas nuevas. Las identidades destino son deterministas por nota/familia/versión, de modo que volver a ejecutar debe reutilizar las notas destino compatibles. Si el tipo o la nota destino ya existen con contenido incompatible, el proceso se detiene en vez de actualizarlo.

`run_live_migration_test.py` ejecuta una prueba contra Anki real a partir de la nota sintética del smoke test: verifica el mapeo de campos, las dos tarjetas destino, la preservación del origen y la idempotencia de una segunda ejecución. La prueba de migración en vivo todavía debe ejecutarse por el usuario en `AJP-SANDBOX`; las pruebas automatizadas locales no sustituyen esa evidencia.

## Actualización de campos de una nota administrada por AJP (v0.6.0)

La operación es distinta de la creación y de la migración de tipo:

- **Crear**: `content_pack.json` crea una nota nueva si su `source_id` aún no está registrado.
- **Migrar**: crea una nueva nota en otro tipo de nota y conserva la nota original.
- **Actualizar campos**: el paquete separado `anki-json-note-update-pack` modifica únicamente campos explícitamente enumerados de una nota ya marcada por AJP.

Reglas de seguridad para actualización:

1. La nota debe encontrarse por el marcador determinista que corresponde a `source_id`, y debe haber exactamente una coincidencia.
2. `expected_model_name` tiene que coincidir literalmente con el tipo de nota actual.
3. Cada campo de `set_fields` debe tener un precondición correspondiente en `expected_fields`.
4. Cada campo modificado debe tener actualmente el valor esperado o el valor de destino (lo que indica que ya se aplicó). Cualquier otro valor causa conflicto.
5. Si hay un conflicto en cualquiera de los objetos del paquete, el preflight aborta todas las escrituras.
6. La escritura exige `--apply` y `--confirm-profile` con el nombre exacto del perfil activo.
7. La operación no cambia el tipo de nota, las plantillas, el mazo, las etiquetas ni el estado de programación. Sin embargo, Anki puede generar o dejar de generar tarjetas si cambiar un campo afecta a una plantilla condicional; para tipos de nota con plantillas condicionales debe inspeccionarse el resultado en un perfil de prueba.
8. AnkiConnect no proporciona una transacción multi-nota. Un error después de algunas escrituras puede dejar una aplicación parcial; el informe JSON y la idempotencia facilitan la recuperación.

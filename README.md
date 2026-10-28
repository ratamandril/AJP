# Anki JSON Protocol (AJP)

AJP define en JSON los paquetes de contenido, los inventarios de colección y los planes de migración o actualización controlada para Anki. Los scripts locales validan esos contratos y realizan operaciones explícitas mediante AnkiConnect.

**Kit actual: v0.7.0** — incluye creación declarativa, inventarios, migración por copia, actualización controlada de campos y planificación de cambios estructurales mediante una nueva versión del tipo de nota.

La documentación principal está en español: [README_ES.md](README_ES.md). Consulta también [las reglas de compatibilidad](docs_COMPATIBILITY_REGLAS_ES.md) y el [procedimiento de cambios estructurales](STRUCTURAL_CHANGES_v0.7.0_ES.md).

## Estado de pruebas

- Las 53 pruebas automatizadas locales pasaron al empaquetar esta versión.
- Las pruebas de integración básica y migración por copia en `AJP-SANDBOX` se reportaron como superadas.
- La prueba estructural en vivo `@1.1.0 → @1.2.0` todavía debe ejecutarse en el Anki local del usuario; las pruebas locales simuladas no la sustituyen.

Los datos de `examples/` son fixtures sintéticos. No representan contenido oficial ni un inventario actual de la colección personal.

## Inicio rápido

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts\validate_content_pack.py examples\content_pack.fisica.json
```

Las operaciones que escriben en Anki deben probarse primero en un perfil de prueba y después de revisar el plan generado. No ejecutes las pruebas en vivo en una colección que contenga información que quieras conservar.

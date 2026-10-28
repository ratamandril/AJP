# AJP v0.7.0 — extensión de cambios estructurales

- Añade plan estructural con contrato de origen para evitar cambios no detectados en el modelo fuente.
- Añade comparación de campos, orden, plantillas y CSS entre origen y destino.
- Añade preflight en vivo de solo lectura limitado a `AJP-SANDBOX`.
- Añade ejecutor estructural que convierte el plan a una migración por copia existente solo después de validar la definición de origen.
- Añade prueba en vivo de una versión sintética `@1.1.0` → `@1.2.0`, con campo `Difficulty`, cambios de plantilla/CSS, verificación del original e idempotencia.
- Incluye 10 pruebas locales simuladas.

#!/usr/bin/env python3
"""Validate an AJP 0.2.0 content pack locally, without contacting Anki."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ajp_core import CONTENT_PACK_VERSION, validate_pack


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", help="Archivo content_pack.json que se validará")
    parser.add_argument("--no-json-schema", action="store_true", help="Omitir validación adicional con jsonschema")
    args = parser.parse_args()
    path = Path(args.pack).expanduser().resolve()
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: no se pudo leer el JSON: {exc}", file=sys.stderr)
        return 2

    errors = validate_pack(pack)
    if not args.no_json_schema:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "content_pack.schema.json"
        try:
            import jsonschema  # type: ignore
        except ImportError:
            print("AVISO: jsonschema no está instalado; se ejecutarán las comprobaciones semánticas integradas.", file=sys.stderr)
        else:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            validator = jsonschema.Draft202012Validator(schema)
            errors.extend(
                f"JSON Schema {err.json_path}: {err.message}"
                for err in sorted(validator.iter_errors(pack), key=lambda item: list(item.path))
            )

    if errors:
        print(f"Paquete INVÁLIDO para AJP {CONTENT_PACK_VERSION}: {len(errors)} problema(s).", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if not isinstance(pack, dict) or pack.get("protocol_version") != CONTENT_PACK_VERSION:
        print(f"Paquete INVÁLIDO: se requiere AJP {CONTENT_PACK_VERSION}.", file=sys.stderr)
        return 1

    print(f"Paquete válido: {pack['pack_id']}")
    print(f"  Mazos declarados: {len(pack['decks'])}")
    print(f"  Tipos de nota declarados: {len(pack['note_types'])}")
    print(f"  Notas declaradas: {len(pack['notes'])}")
    print("  No se contactó con Anki ni se modificó ninguna colección.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

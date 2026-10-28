#!/usr/bin/env python3
"""Validate and print a non-executing AJP migration plan (no writes to Anki)."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from ajp_core import _refs_in_template

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "migration_plan.schema.json"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"No se pudo leer JSON {path}: {exc}") from exc


def source_tag(source_id: str) -> str:
    return "ajp_uid_" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]


def semver_core(version: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(version)
    if not match:
        raise ValueError(f"Versión no válida: {version!r}")
    return tuple(int(match.group(i)) for i in (1, 2, 3))


def validate_plan(plan: Any, snapshot: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    summary: dict[str, Any] = {"validation_mode": "with_snapshot" if snapshot else "structure_only", "selected_notes": []}
    if not isinstance(plan, dict):
        return ["La raíz del plan debe ser un objeto JSON."], summary

    # Validate against JSON Schema when jsonschema is installed; cross-field checks below
    # remain useful even if the optional dependency is missing.
    try:
        import jsonschema  # type: ignore
        schema = load_json(SCHEMA_PATH)
        errors.extend(f"Esquema: {err.message} (ruta: {'/'.join(map(str, err.absolute_path)) or '/'})"
                      for err in jsonschema.Draft202012Validator(schema).iter_errors(plan))
    except ImportError:
        summary["json_schema_validation"] = "skipped: instala requirements.txt para usar jsonschema"

    if errors:
        return errors, summary

    source = plan["source"]
    target = plan["target_note_type"]
    if source["type_id"] != target["type_id"]:
        errors.append("type_id de origen y destino debe ser el mismo: es una nueva versión de la misma familia de tipo.")
    try:
        from_version = semver_core(source["version"])
        to_version = semver_core(target["version"])
        if to_version <= from_version:
            errors.append("La versión de destino debe ser mayor que la versión de origen.")
    except ValueError as exc:
        errors.append(str(exc))

    if source["anki_model_name"] == target["anki_model_name"]:
        errors.append("El nombre del tipo Anki destino debe ser distinto; este plan crea una versión paralela y no modifica el tipo original.")
    if f"@{source['version']}" not in source["anki_model_name"]:
        errors.append("El nombre del modelo de origen debe llevar marcador @<versión>, por ejemplo AJP::tipo@1.0.0.")
    if f"@{target['version']}" not in target["anki_model_name"]:
        errors.append("El nombre del modelo destino debe llevar marcador @<versión>, por ejemplo AJP::tipo@1.1.0.")
    if f"{source['type_id']}@{source['version']}" not in source["anki_model_name"]:
        errors.append("El nombre del modelo de origen no coincide con type_id y versión declarados.")
    if f"{target['type_id']}@{target['version']}" not in target["anki_model_name"]:
        errors.append("El nombre del modelo destino no coincide con type_id y versión declarados.")

    target_fields = target["fields"]
    required_fields = set(target["required_fields"])
    if len(target_fields) != len(set(target_fields)):
        errors.append("fields de destino contiene nombres repetidos.")
    if required_fields - set(target_fields):
        errors.append(f"required_fields de destino no declarados en fields: {sorted(required_fields - set(target_fields))}.")
    target_template_names = [item["name"] for item in target["card_templates"]]
    target_template_ids = [item["template_id"] for item in target["card_templates"]]
    if len(target_template_names) != len(set(target_template_names)):
        errors.append("Las plantillas destino tienen nombres repetidos.")
    if len(target_template_ids) != len(set(target_template_ids)):
        errors.append("Las plantillas destino tienen template_id repetidos.")
    for template in target["card_templates"]:
        for html_key in ("question_html", "answer_html"):
            unknown = _refs_in_template(template[html_key], set(target_fields))
            if unknown:
                errors.append(f"La plantilla destino {template['name']!r}.{html_key} usa campos desconocidos: {sorted(set(unknown))}.")
    mapped_fields = [m["target_field"] for m in plan["field_mapping"]]
    if len(mapped_fields) != len(set(mapped_fields)):
        errors.append("Cada target_field debe aparecer una sola vez en field_mapping.")
    if set(mapped_fields) != set(target_fields):
        missing = sorted(set(target_fields) - set(mapped_fields))
        unknown = sorted(set(mapped_fields) - set(target_fields))
        if missing:
            errors.append(f"No hay mapeo para todos los campos destino; faltan: {missing}.")
        if unknown:
            errors.append(f"El mapeo contiene campos destino no declarados: {unknown}.")
    if plan["selection"]["mode"] != "explicit_source_ids":
        errors.append("Solo se permite seleccionar por source_id explícitos en esta versión.")

    snapshot_fields: set[str] | None = None
    if snapshot is not None:
        model_matches = [m for m in snapshot.get("note_types", []) if m.get("name") == source["anki_model_name"]]
        if len(model_matches) != 1:
            errors.append(f"Snapshot: se esperaba un tipo de nota de origen llamado {source['anki_model_name']!r}; se encontraron {len(model_matches)}.")
        else:
            snapshot_fields = {field.get("name") for field in model_matches[0].get("fields", []) if isinstance(field, dict)}
            if not snapshot_fields:
                snapshot_fields = set(model_matches[0].get("fields", [])) if isinstance(model_matches[0].get("fields"), list) else set()
            summary["source_fields_in_snapshot"] = sorted(snapshot_fields)

        note_lookup: dict[str, list[dict[str, Any]]] = {}
        for source_id in plan["selection"]["source_ids"]:
            hashed_tag = source_tag(source_id)
            matches = [n for n in snapshot.get("notes", [])
                       if n.get("note_type") == source["anki_model_name"] and hashed_tag in (n.get("tags") or [])]
            note_lookup[source_id] = matches
            if len(matches) != 1:
                errors.append(f"Snapshot: source_id {source_id!r} debería corresponder a una nota AJP marcada dentro del tipo de origen; encontradas: {len(matches)}.")
            else:
                summary["selected_notes"].append({"source_id": source_id, "note_id": matches[0].get("note_id")})
                available_fields = set((matches[0].get("fields") or {}).keys())
                for mapping in plan["field_mapping"]:
                    if mapping["operation"] == "copy" and mapping["source_field"] not in available_fields:
                        errors.append(f"Snapshot: la nota {source_id!r} no contiene el campo de origen {mapping['source_field']!r}.")
        summary["source_model_note_count_in_snapshot"] = sum(1 for n in snapshot.get("notes", []) if n.get("note_type") == source["anki_model_name"])
    elif snapshot_fields is not None:
        pass

    # In a checked snapshot, all source-field references must exist on the type itself.
    if snapshot_fields is not None:
        for mapping in plan["field_mapping"]:
            if mapping["operation"] == "copy" and mapping["source_field"] not in snapshot_fields:
                errors.append(f"El campo de origen {mapping['source_field']!r} no existe en el tipo de nota declarado por el snapshot.")

    summary.update({
        "plan_id": plan["plan_id"],
        "source": {"type_id": source["type_id"], "version": source["version"], "anki_model_name": source["anki_model_name"]},
        "target": {"type_id": target["type_id"], "version": target["version"], "anki_model_name": target["anki_model_name"]},
        "source_ids": list(plan["selection"]["source_ids"]),
        "field_mapping_count": len(plan["field_mapping"]),
        "target_deck": plan.get("target_deck"),
        "execution": "PLAN_ONLY — no se conecta a Anki y no escribe ni actualiza nada",
        "migration_strategy": "copy_to_new_notes; los originales se conservan; la programación de repaso no se transfiere"
    })
    return errors, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="Archivo migration_plan.json")
    parser.add_argument("--snapshot", help="Snapshot completo de Anki para verificar tipo origen, notas y campos")
    parser.add_argument("--out", help="Guardar el informe JSON del plan")
    args = parser.parse_args()
    try:
        plan = load_json(Path(args.plan).expanduser().resolve())
        snapshot = load_json(Path(args.snapshot).expanduser().resolve()) if args.snapshot else None
        errors, summary = validate_plan(plan, snapshot)
        if errors:
            print("PLAN INVÁLIDO:")
            for error in errors:
                print(f"  - {error}")
            summary["result"] = "invalid"
            summary["errors"] = errors
            code = 1
        else:
            summary["result"] = "valid_plan_only"
            print("PLAN VÁLIDO (solo planificación; no se ejecuta ninguna migración).")
            print(f"Plan: {summary['plan_id']}")
            print(f"Origen: {summary['source']['anki_model_name']} (versión {summary['source']['version']})")
            print(f"Destino paralelo: {summary['target']['anki_model_name']} (versión {summary['target']['version']})")
            print(f"Mazo destino: {summary.get('target_deck')}")
            print(f"Notas de origen seleccionadas: {len(summary['source_ids'])}")
            print(f"Campos destino mapeados: {summary['field_mapping_count']}")
            if snapshot:
                print(f"Notas seleccionadas verificadas en snapshot: {len(summary['selected_notes'])}")
            print("Política: conservar originales; las nuevas notas empiezan con programación de repaso nueva.")
            print("IMPORTANTE: este validador NO cambia Anki, no crea tipos y no copia notas.")
            code = 0
        if args.out:
            out = Path(args.out).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Informe escrito: {out}")
        return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

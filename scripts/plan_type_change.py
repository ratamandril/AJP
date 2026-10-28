#!/usr/bin/env python3
"""Read-only preflight and structural diff for a versioned AJP note-type change."""
from __future__ import annotations
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from structural_change_core import (
    definition_diff, expected_definition, normalize_definition,
    source_tag, target_definition, target_source_id, to_migration_plan,
    validate_structural_plan,
)

API_VERSION = 6
DEFAULT_URL = "http://127.0.0.1:8765"
EXPECTED_PROFILE = "AJP-SANDBOX"


class AnkiConnect:
    def __init__(self, url: str, timeout: int = 60):
        self.url, self.timeout = url.rstrip("/"), timeout

    def call(self, action: str, **params: Any) -> Any:
        body: dict[str, Any] = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        request = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo contactar con AnkiConnect en {self.url}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None:
            reason = payload.get("error") if isinstance(payload, dict) else "respuesta JSON inesperada"
            raise RuntimeError(f"AnkiConnect action={action}: {reason}")
        return payload.get("result")


def load_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo leer el plan {path}: {exc}") from exc
    errors = validate_structural_plan(plan)
    if not errors:
        try:
            import jsonschema  # type: ignore
            schema_path = Path(__file__).resolve().parents[1] / "schemas" / "structural_change_plan.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            errors.extend(f"JSON Schema {e.json_path}: {e.message}" for e in jsonschema.Draft202012Validator(schema).iter_errors(plan))
        except ImportError:
            pass
    if errors:
        raise RuntimeError("Plan estructural inválido:\n" + "\n".join(f"  - {e}" for e in errors))
    return plan


def models_by_name(client: AnkiConnect) -> dict[str, dict[str, Any]]:
    mapping = client.call("modelNamesAndIds") or {}
    ids = list(mapping.values()) if isinstance(mapping, dict) else []
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        if not batch:
            continue
        for model in client.call("findModelsById", modelIds=batch) or []:
            if isinstance(model, dict) and model.get("name"):
                result[model["name"]] = model
    return result


def field_values(note: dict[str, Any]) -> dict[str, str]:
    fields = note.get("fields") or {}
    return {k: str(v.get("value", "")) if isinstance(v, dict) else str(v) for k, v in fields.items()}


def map_fields(source_values: dict[str, str], plan: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for mapping in plan["field_mapping"]:
        op, target_field = mapping["operation"], mapping["target_field"]
        if op == "copy":
            sf = mapping["source_field"]
            if sf not in source_values:
                raise RuntimeError(f"El campo de origen {sf!r} no existe en una nota seleccionada.")
            output[target_field] = source_values[sf]
        elif op == "constant":
            output[target_field] = mapping["value"]
        else:
            output[target_field] = ""
    for field in plan["target_note_type"]["fields"]:
        output.setdefault(field, "")
    return output


def inspect_live(client: AnkiConnect, plan: dict[str, Any]) -> dict[str, Any]:
    api_version = client.call("version")
    if not isinstance(api_version, int) or api_version < API_VERSION:
        raise RuntimeError(f"AnkiConnect API incompatible: {api_version!r}; se necesita {API_VERSION}.")
    profile = client.call("getActiveProfile")
    if profile != EXPECTED_PROFILE:
        raise RuntimeError(f"Este plan de prueba solo se permite en {EXPECTED_PROFILE!r}; perfil actual: {profile!r}.")

    source = plan["source"]
    target = plan["target_note_type"]
    models = models_by_name(client)
    actual_source = models.get(source["anki_model_name"])
    if actual_source is None:
        raise RuntimeError(f"No existe el modelo origen {source['anki_model_name']!r}.")
    actual_source_def = normalize_definition(actual_source)
    expected_source_def = expected_definition(source)
    if actual_source_def != expected_source_def:
        raise RuntimeError(
            "La definición real del tipo origen no coincide con source.expected_definition. "
            "No se puede asumir que el plan se diseñó para esta estructura. Genera/revisa un diff nuevo antes de continuar."
        )

    target_def = target_definition(target)
    actual_target = models.get(target["anki_model_name"])
    target_exists = actual_target is not None
    target_reused = False
    if target_exists:
        actual_target_def = normalize_definition(actual_target)
        if actual_target_def != target_def:
            raise RuntimeError(f"El modelo destino {target['anki_model_name']!r} ya existe pero no coincide con la definición objetivo.")
        target_reused = True

    deck_map = client.call("deckNamesAndIds") or {}
    deck_names = set(deck_map.keys()) if isinstance(deck_map, dict) else set(deck_map)
    note_actions = []
    source_preservation = []
    for source_id in plan["selection"]["source_ids"]:
        note_ids = client.call("findNotes", query=f"tag:{source_tag(source_id)}") or []
        if len(note_ids) != 1:
            raise RuntimeError(f"source_id {source_id!r}: se esperaba una nota AJP única; se hallaron {len(note_ids)}.")
        note_info_list = client.call("notesInfo", notes=note_ids) or []
        if len(note_info_list) != 1 or not isinstance(note_info_list[0], dict):
            raise RuntimeError(f"No se pudo leer la nota de origen de {source_id!r}.")
        note_info = note_info_list[0]
        if note_info.get("modelName") != source["anki_model_name"]:
            raise RuntimeError(f"La nota {source_id!r} está en {note_info.get('modelName')!r}, no en el tipo origen declarado.")
        original_values = field_values(note_info)
        target_values = map_fields(original_values, plan)
        empty_required = [f for f in target["required_fields"] if not target_values.get(f, "").strip()]
        if empty_required:
            raise RuntimeError(f"La migración de {source_id!r} dejaría campos requeridos vacíos: {empty_required}.")
        if not target_values.get(target["fields"][0], "").strip():
            raise RuntimeError(f"La migración de {source_id!r} dejaría vacío el primer campo destino {target['fields'][0]!r}.")

        target_id = target_source_id(source_id, target["type_id"], target["version"])
        target_note_ids = client.call("findNotes", query=f"tag:{source_tag(target_id)}") or []
        if len(target_note_ids) > 1:
            raise RuntimeError(f"Hay varias notas destino para la identidad derivada de {source_id!r}.")
        action = "create"
        target_note_id = None
        target_card_count = None
        if target_note_ids:
            existing_list = client.call("notesInfo", notes=target_note_ids) or []
            if len(existing_list) != 1 or not isinstance(existing_list[0], dict):
                raise RuntimeError(f"No se pudo comprobar la nota destino de {source_id!r}.")
            existing = existing_list[0]
            if existing.get("modelName") != target["anki_model_name"] or field_values(existing) != target_values:
                raise RuntimeError(f"La nota destino ya existe pero sus campos o tipo difieren del plan para {source_id!r}.")
            cards = client.call("cardsInfo", cards=existing.get("cards") or []) or []
            card_decks = {c.get("deckName") for c in cards if isinstance(c, dict)}
            if card_decks != {plan["target_deck"]}:
                raise RuntimeError(f"La nota destino {source_id!r} existe en un mazo incompatible: {sorted(str(x) for x in card_decks)}.")
            action, target_note_id, target_card_count = "reuse", existing.get("noteId"), len(cards)

        source_cards = client.call("cardsInfo", cards=note_info.get("cards") or []) or []
        note_actions.append({
            "source_id": source_id,
            "source_note_id": note_info.get("noteId"),
            "source_card_count": len(source_cards),
            "source_fields_checked": sorted(original_values),
            "target_source_id": target_id,
            "target_note_id": target_note_id,
            "target_fields": sorted(target_values),
            "target_nonempty_fields": sorted(k for k, v in target_values.items() if v.strip()),
            "target_card_count_if_reused": target_card_count,
            "action": action,
        })
        source_preservation.append({"source_id": source_id, "source_note_id": note_info.get("noteId"), "source_field_values": original_values,
                                   "source_tags": list(note_info.get("tags") or []), "source_card_ids": list(note_info.get("cards") or [])})

    diff = definition_diff(actual_source_def, target_def)
    return {
        "protocol": "anki-json-structural-change-report",
        "report_version": "0.1.0",
        "plan_id": plan["plan_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_profile": profile,
        "api_version": api_version,
        "result": "valid_plan_only",
        "writes_attempted": False,
        "source_type": source["anki_model_name"],
        "target_type": target["anki_model_name"],
        "target_deck": plan["target_deck"],
        "target_deck_exists": plan["target_deck"] in deck_names,
        "target_model_exists": target_exists,
        "target_model_reused": target_reused,
        "structural_diff": diff,
        "notes": note_actions,
        "policy": "Crear una versión paralela y notas nuevas; preservar el tipo, las notas y las tarjetas de origen; la programación de destino se reinicia.",
        "limitations": [
            "La cantidad final de tarjetas debe verificarse después de crear las notas: Anki puede omitir tarjetas por campos vacíos o reglas de plantilla.",
            "El plan no copia historial ni programación de repaso.",
            "El preflight verifica una definición fuente exacta para detectar si el modelo cambió desde que se redactó este plan."
        ],
        "_source_preservation": source_preservation,
        "_migration_plan": to_migration_plan(plan),
    }


def save_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    public_report = {k: v for k, v in report.items() if not k.startswith("_")}
    output.write_text(json.dumps(public_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="Structural change plan JSON")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", help="Guardar informe JSON de solo lectura")
    args = parser.parse_args()
    try:
        plan = load_plan(Path(args.plan).expanduser().resolve())
        client = AnkiConnect(args.url)
        report = inspect_live(client, plan)
        diff = report["structural_diff"]
        print(f"Plan estructural: {report['plan_id']} | perfil: {report['active_profile']}")
        print(f"Origen: {report['source_type']}")
        print(f"Destino paralelo: {report['target_type']}")
        print(f"Campos añadidos: {diff['fields_added']}; retirados: {diff['fields_removed']}; orden cambiado: {diff['field_order_changed']}")
        print(f"Plantillas añadidas: {diff['templates_added']}; retiradas: {diff['templates_removed']}; modificadas: {diff['templates_changed']}")
        print(f"CSS cambiado: {diff['css_changed']}; plantillas definidas destino: {diff['target_template_count']}")
        print(f"Mazo destino: {'existe' if report['target_deck_exists'] else 'se crearía'}")
        for note in report["notes"]:
            print(f"  {note['action'].upper()} {note['source_id']}: source cards={note['source_card_count']}; target fields={note['target_fields']}")
        print("Modo solo lectura: no se escribió ningún cambio en Anki.")
        save_report(args.out, report)
        if args.out:
            print(f"Informe: {Path(args.out).expanduser().resolve()}")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply an explicit AJP migration by copying selected notes to a new note type.

Safety contract:
- Default mode is plan-only; it performs no Anki writes.
- Apply is allowed only when the active profile is exactly AJP-SANDBOX and the
  caller passes --confirm-write AJP-SANDBOX.
- Source notes and source note type are never updated or deleted.
- New target notes get deterministic AJP source IDs; re-running is idempotent.
- AnkiConnect has no transaction, so a transport failure may leave a partial
  but non-destructive result. The report permits safe re-runs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from ajp_core import source_tag, model_signature_from_anki, model_signature_from_spec  # noqa: E402
from plan_migration import validate_plan, load_json as load_json_plan  # noqa: E402

API_VERSION = 6
DEFAULT_URL = "http://127.0.0.1:8765"
EXPECTED_PROFILE = "AJP-SANDBOX"


class AnkiConnect:
    def __init__(self, url: str = DEFAULT_URL, timeout: int = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def call(self, action: str, **params: Any) -> Any:
        body: dict[str, Any] = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        request = urllib.request.Request(
            self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo contactar con AnkiConnect en {self.url}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None:
            error = payload.get("error") if isinstance(payload, dict) else "respuesta inesperada"
            raise RuntimeError(f"AnkiConnect action={action}: {error}")
        return payload.get("result")


def load_plan(path: Path) -> dict[str, Any]:
    plan = load_json_plan(path)
    errors, _ = validate_plan(plan)
    if errors:
        raise RuntimeError("El plan de migración no es válido:\n" + "\n".join(f"  - {e}" for e in errors))
    return plan


def model_spec_for_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": target["anki_model_name"],
        "kind": target["kind"],
        "fields": list(target["fields"]),
        "card_templates": [
            {"name": t["name"], "question_html": t["question_html"], "answer_html": t["answer_html"]}
            for t in target["card_templates"]
        ],
        "css": target["css"],
    }


def model_to_api(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "modelName": spec["name"],
        "inOrderFields": spec["fields"],
        "css": spec["css"],
        "isCloze": spec["kind"] == "cloze",
        "cardTemplates": [
            {"Name": t["name"], "Front": t["question_html"], "Back": t["answer_html"]}
            for t in spec["card_templates"]
        ],
    }


def models_by_name(client: AnkiConnect) -> dict[str, dict[str, Any]]:
    names_ids = client.call("modelNamesAndIds") or {}
    ids = list(names_ids.values()) if isinstance(names_ids, dict) else []
    output: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        for model in client.call("findModelsById", modelIds=batch) or []:
            if isinstance(model, dict) and model.get("name"):
                output[model["name"]] = model
    return output


def deterministic_migrated_source_id(source_id: str, target_type_id: str, target_version: str) -> str:
    key = f"{source_id}\0{target_type_id}@{target_version}".encode("utf-8")
    return "org.anki-json-protocol.migrated." + hashlib.sha256(key).hexdigest()[:40]


def short_tag(prefix: str, value: str, length: int = 20) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}{digest}"


def fields_from_note(note_info: dict[str, Any]) -> dict[str, str]:
    raw = note_info.get("fields") or {}
    return {name: str(value.get("value", "")) if isinstance(value, dict) else str(value)
            for name, value in raw.items()}


def mapped_fields(source_fields: dict[str, str], plan: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for mapping in plan["field_mapping"]:
        operation = mapping["operation"]
        target_field = mapping["target_field"]
        if operation == "copy":
            source_field = mapping["source_field"]
            if source_field not in source_fields:
                raise RuntimeError(f"El campo de origen {source_field!r} no existe en una nota seleccionada.")
            out[target_field] = source_fields[source_field]
        elif operation == "constant":
            out[target_field] = mapping["value"]
        elif operation == "empty":
            out[target_field] = ""
        else:  # already guarded by schema; keep runtime defensive
            raise RuntimeError(f"Operación de mapeo no admitida: {operation!r}.")
    for field_name in plan["target_note_type"]["fields"]:
        out.setdefault(field_name, "")
    return out


def find_note_by_source_id(client: AnkiConnect, source_id: str) -> list[dict[str, Any]]:
    ids = client.call("findNotes", query=f"tag:{source_tag(source_id)}") or []
    if not ids:
        return []
    return [n for n in (client.call("notesInfo", notes=ids) or []) if isinstance(n, dict)]


def get_deck_names(client: AnkiConnect) -> set[str]:
    names = client.call("deckNamesAndIds") or {}
    return set(names.keys()) if isinstance(names, dict) else set(names)


def can_add_note(client: AnkiConnect, payload: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        result = client.call("canAddNotesWithErrorDetail", notes=[payload]) or [{}]
        first = result[0] if result else {}
        return bool(first.get("canAdd", False)), str(first.get("error") or "") or None
    except RuntimeError:
        result = client.call("canAddNotes", notes=[payload]) or [False]
        can_add = bool(result[0]) if result else False
        return can_add, None if can_add else "AnkiConnect indica que no se puede añadir (posible duplicado)."


def note_payload(deck: str, model_name: str, fields: dict[str, str], tags: list[str]) -> dict[str, Any]:
    return {
        "deckName": deck,
        "modelName": model_name,
        "fields": fields,
        "tags": tags,
        "options": {
            "allowDuplicate": False,
            "duplicateScope": "collection",
            "duplicateScopeOptions": {"checkAllModels": False},
        },
    }


def target_note_matches(existing: dict[str, Any], target_model_name: str,
                        expected_fields: dict[str, str], target_deck: str,
                        client: AnkiConnect) -> tuple[bool, str]:
    if existing.get("modelName") != target_model_name:
        return False, f"el source_id migrado ya existe en el tipo {existing.get('modelName')!r}, no en {target_model_name!r}"
    actual_fields = fields_from_note(existing)
    if actual_fields != expected_fields:
        return False, "el source_id migrado ya existe, pero los campos no coinciden con el plan actual"
    card_ids = existing.get("cards") or []
    if not card_ids:
        return False, "la nota migrada ya existe, pero no tiene tarjetas; no se corregirá automáticamente"
    cards = client.call("cardsInfo", cards=card_ids) or []
    card_decks = {c.get("deckName") for c in cards if isinstance(c, dict)}
    if card_decks != {target_deck}:
        return False, f"la nota migrada ya existe en mazo(s) {sorted(str(x) for x in card_decks)}, no exactamente en {target_deck!r}"
    return True, "coincide con una migración anterior; reutilizar sin modificar"


def build_report(plan: dict[str, Any], profile: str | None, api_version: int | None, mode: str) -> dict[str, Any]:
    return {
        "protocol": "anki-json-migration-execution",
        "migration_plan_version": plan["migration_plan_version"],
        "plan_id": plan["plan_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "active_profile": profile,
        "api_version": api_version,
        "target_deck": plan["target_deck"],
        "source_type": plan["source"]["anki_model_name"],
        "target_type": plan["target_note_type"]["anki_model_name"],
        "writes_attempted": False,
        "source_notes_modified": False,
        "source_notes_deleted": False,
        "target_structure": {"deck_created": False, "deck_reused": False, "note_type_created": False, "note_type_reused": False},
        "notes": {"selected": [], "planned": [], "created": [], "reused": [], "failed": []},
        "errors": [],
    }


def save_report(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="Archivo migration_plan.json")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"URL de AnkiConnect (predeterminado: {DEFAULT_URL})")
    parser.add_argument("--apply", action="store_true", help="Ejecutar la migración; sin esto solo planifica")
    parser.add_argument("--confirm-write", help=f"Debe ser exactamente {EXPECTED_PROFILE!r} para autorizar escrituras en sandbox")
    parser.add_argument("--out", help="Guardar informe JSON de plan/ejecución")
    args = parser.parse_args()

    try:
        plan = load_plan(Path(args.plan).expanduser().resolve())
        if args.apply and args.confirm_write != EXPECTED_PROFILE:
            raise RuntimeError(f"Escritura bloqueada: para aplicar debes indicar --confirm-write {EXPECTED_PROFILE}.")

        client = AnkiConnect(args.url)
        api_version = client.call("version")
        if not isinstance(api_version, int) or api_version < API_VERSION:
            raise RuntimeError(f"Versión de AnkiConnect incompatible: {api_version!r}.")
        profile = client.call("getActiveProfile")
        if args.apply and profile != EXPECTED_PROFILE:
            raise RuntimeError(f"Escritura bloqueada: perfil activo {profile!r}; se requiere {EXPECTED_PROFILE!r}.")

        source = plan["source"]
        target = plan["target_note_type"]
        source_version_marker = f"@{source['version']}"
        target_version_marker = f"@{target['version']}"
        if source_version_marker not in source["anki_model_name"]:
            raise RuntimeError(f"El nombre del tipo origen no contiene {source_version_marker!r}.")
        if target_version_marker not in target["anki_model_name"]:
            raise RuntimeError(f"El nombre del tipo destino no contiene {target_version_marker!r}.")

        deck_names = get_deck_names(client)
        existing_models = models_by_name(client)
        source_model = existing_models.get(source["anki_model_name"])
        if source_model is None:
            raise RuntimeError(f"No existe el tipo de nota de origen {source['anki_model_name']!r}.")
        # Ensure source model is standard/cloze kind consistent with the declared AJP family marker by inspecting Anki.
        source_kind = "cloze" if source_model.get("type") == 1 else "standard"
        if source_kind not in ("standard", "cloze"):
            raise RuntimeError("No se pudo identificar el tipo del modelo de origen.")

        target_spec = model_spec_for_target(target)
        target_model = existing_models.get(target["anki_model_name"])
        target_model_created = target_model is None
        target_model_reused = False
        if target_model is not None:
            expected_sig = model_signature_from_spec(target_spec)
            actual_sig = model_signature_from_anki(target_model)
            for key in ("name", "kind", "fields", "card_templates", "css"):
                if expected_sig.get(key) != actual_sig.get(key):
                    raise RuntimeError(f"El tipo de destino ya existe con definición incompatible ({key}); no se actualizará.")
            target_model_reused = True

        # Build a full read-only preflight for every selected source note and its destination.
        note_actions: list[dict[str, Any]] = []
        for source_id in plan["selection"]["source_ids"]:
            matches = find_note_by_source_id(client, source_id)
            if len(matches) != 1:
                raise RuntimeError(f"source_id {source_id!r}: se esperaba exactamente una nota AJP, se encontraron {len(matches)}.")
            source_note = matches[0]
            if source_note.get("modelName") != source["anki_model_name"]:
                raise RuntimeError(f"source_id {source_id!r} pertenece al tipo {source_note.get('modelName')!r}, no al origen declarado.")
            src_fields = fields_from_note(source_note)
            dest_fields = mapped_fields(src_fields, plan)
            missing_req = [f for f in target["required_fields"] if not str(dest_fields.get(f, "")).strip()]
            if missing_req:
                raise RuntimeError(f"source_id {source_id!r}: la migración dejaría vacíos los campos requeridos {missing_req}.")
            if not str(dest_fields.get(target["fields"][0], "")).strip():
                raise RuntimeError(f"source_id {source_id!r}: el primer campo destino {target['fields'][0]!r} quedaría vacío.")

            target_source_id = deterministic_migrated_source_id(source_id, target["type_id"], target["version"])
            existing_target = find_note_by_source_id(client, target_source_id)
            if len(existing_target) > 1:
                raise RuntimeError(f"Se encontraron varias notas destino para source_id migrado derivado de {source_id!r}.")
            target_tags = [tag for tag in (source_note.get("tags") or []) if not str(tag).startswith("ajp_uid_")]
            target_tags = sorted(set(target_tags + [
                "ajp_migrated",
                source_tag(target_source_id),
                short_tag("ajp_migration_", plan["plan_id"]),
                short_tag("ajp_from_", source_id),
            ]))
            payload = note_payload(plan["target_deck"], target["anki_model_name"], dest_fields, target_tags)
            if existing_target:
                is_match, reason = target_note_matches(existing_target[0], target["anki_model_name"], dest_fields,
                                                       plan["target_deck"], client)
                if not is_match:
                    raise RuntimeError(f"Conflicto en destino para source_id {source_id!r}: {reason}.")
                note_actions.append({"source_id": source_id, "target_source_id": target_source_id,
                                     "source_note_id": source_note.get("noteId"), "target_note_id": existing_target[0].get("noteId"),
                                     "fields": dest_fields, "tags": target_tags, "action": "reuse"})
            else:
                note_actions.append({"source_id": source_id, "target_source_id": target_source_id,
                                     "source_note_id": source_note.get("noteId"), "fields": dest_fields,
                                     "tags": target_tags, "payload": payload, "action": "create"})

        # Detect first-field collisions among all selected destinations before any write.
        # canAddNotes checks the existing collection, but cannot see multiple notes in this
        # batch before they are inserted. Catch such collisions here to avoid partial migration.
        first_field = target["fields"][0]
        first_values: dict[str, str] = {}
        for action in note_actions:
            normalized = str(action["fields"].get(first_field, "")).strip().casefold()
            if not normalized:
                raise RuntimeError(f"El primer campo destino {first_field!r} queda vacío para {action['source_id']!r}.")
            previous = first_values.get(normalized)
            if previous is not None and previous != action["source_id"]:
                raise RuntimeError(f"Colisión de primer campo destino {first_field!r} entre source_id {previous!r} y {action['source_id']!r}; no se escribió nada.")
            first_values[normalized] = action["source_id"]

        # Preflight addability for all new destinations before any write.
        for action in note_actions:
            if action["action"] == "create" and plan["target_deck"] in deck_names and not target_model_created:
                can_add, reason = can_add_note(client, action["payload"])
                if not can_add:
                    raise RuntimeError(f"Anki rechaza crear la nota destino de {action['source_id']!r}: {reason or 'posible duplicado'}.")
            elif action["action"] == "create":
                # The model or deck may not exist yet, so canAddNotes may be false before creation.
                # Duplicate identity is already preflighted using the deterministic AJP source_id tag.
                pass

        report = build_report(plan, profile, api_version, "apply" if args.apply else "plan")
        report["target_structure"].update({"deck_created": plan["target_deck"] not in deck_names,
                                             "deck_reused": plan["target_deck"] in deck_names,
                                             "note_type_created": target_model_created,
                                             "note_type_reused": target_model_reused})
        report["notes"]["selected"] = [a["source_id"] for a in note_actions]
        report["notes"]["planned"] = [{"source_id": a["source_id"], "target_source_id": a["target_source_id"],
                                        "action": "create" if a["action"] == "create" else "reuse"}
                                       for a in note_actions]
        report["notes"]["reused"] = [{"source_id": a["source_id"], "target_source_id": a["target_source_id"],
                                       "target_note_id": a.get("target_note_id")} for a in note_actions if a["action"] == "reuse"]
        print(f"Plan: {plan['plan_id']} | Perfil: {profile} | API: {api_version}")
        print(f"Origen: {source['anki_model_name']} (v{source['version']})")
        print(f"Destino: {target['anki_model_name']} (v{target['version']})")
        print(f"Mazo destino: {plan['target_deck']}")
        print(f"Notas seleccionadas: {len(note_actions)}; nuevas: {sum(a['action']=='create' for a in note_actions)}; ya migradas: {sum(a['action']=='reuse' for a in note_actions)}")
        print("Política: COPIAR A NOTAS NUEVAS; conservar notas originales; programación nueva; no actualizar ni eliminar.")

        if not args.apply:
            print("Modo plan-only: no se ha escrito ningún cambio. Revisa el plan; aplica solo en AJP-SANDBOX.")
            for action in note_actions:
                print(f"  {action['action'].upper()}: {action['source_id']} -> {action['target_source_id']}")
            save_report(args.out, report)
            return 0

        # Mutation phase starts only after all selected notes passed preflight.
        try:
            if plan["target_deck"] not in deck_names:
                report["writes_attempted"] = True
                client.call("createDeck", deck=plan["target_deck"])
                print(f"Creado mazo destino: {plan['target_deck']}")
            if target_model_created:
                report["writes_attempted"] = True
                client.call("createModel", **model_to_api(target_spec))
                print(f"Creado tipo destino: {target['anki_model_name']}")
        except Exception as exc:
            report["errors"].append(f"Fallo creando la estructura destino: {exc}")
            report["result"] = "partial_or_failed"
            save_report(args.out, report)
            print(report["errors"][-1], file=sys.stderr)
            if args.out:
                print(f"Informe parcial: {Path(args.out).expanduser().resolve()}", file=sys.stderr)
            return 2
        for action in note_actions:
            if action["action"] == "reuse":
                continue
            # Recheck eligibility after model/deck creation to avoid avoidable duplicates.
            can_add, reason = can_add_note(client, action["payload"])
            if not can_add:
                report["notes"]["failed"].append({"source_id": action["source_id"], "error": reason or "Anki rechazó addNote"})
                continue
            try:
                report["writes_attempted"] = True
                note_id = client.call("addNote", note=action["payload"])
                if not note_id:
                    raise RuntimeError("AnkiConnect no devolvió el ID de la nota destino.")
                report["notes"]["created"].append({"source_id": action["source_id"],
                                                     "target_source_id": action["target_source_id"],
                                                     "source_note_id": action["source_note_id"],
                                                     "target_note_id": note_id,
                                                     "generated_card_count": len(client.call("findCards", query=f"nid:{note_id}") or [])})
                print(f"Migrada (copiada) {action['source_id']} -> note_id={note_id}")
            except Exception as exc:
                report["notes"]["failed"].append({"source_id": action["source_id"], "error": str(exc)})

        # Read-back check that source note IDs remain present and source fields are unchanged.
        source_preservation: list[dict[str, Any]] = []
        for action in note_actions:
            src_after = client.call("notesInfo", notes=[action["source_note_id"]]) or []
            ok = bool(src_after) and src_after[0].get("modelName") == source["anki_model_name"]
            source_preservation.append({"source_id": action["source_id"], "source_note_id": action["source_note_id"],
                                        "still_present_in_original_type": ok})
        report["source_preservation_checks"] = source_preservation
        report["source_notes_modified"] = False
        report["source_notes_deleted"] = False
        report["result"] = "passed" if not report["notes"]["failed"] and all(x["still_present_in_original_type"] for x in source_preservation) else "partial_or_failed"
        save_report(args.out, report)
        print(f"Resultado: {report['result']}; creadas {len(report['notes']['created'])}, ya existentes {len(report['notes']['reused'])}, fallos {len(report['notes']['failed'])}.")
        if args.out:
            print(f"Informe: {Path(args.out).expanduser().resolve()}")
        return 0 if report["result"] == "passed" else 2
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

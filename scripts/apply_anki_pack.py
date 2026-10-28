#!/usr/bin/env python3
"""Validate and apply an AJP 0.2.0 content pack through AnkiConnect.

Default mode is a read-only plan. --apply may create decks, note types, and notes.
The program never updates or deletes an existing object or review state.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ajp_core import (
    CONTENT_PACK_VERSION,
    model_signature_from_anki,
    model_signature_from_spec,
    source_tag,
    validate_pack,
)

API_VERSION = 6
DEFAULT_URL = "http://127.0.0.1:8765"


class AnkiConnect:
    def __init__(self, url: str, timeout: int = 90):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def call(self, action: str, **params: Any) -> Any:
        body: dict[str, Any] = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo contactar con AnkiConnect en {self.url}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None:
            reason = payload.get("error") if isinstance(payload, dict) else "respuesta JSON inesperada"
            raise RuntimeError(f"AnkiConnect action={action}: {reason}")
        return payload.get("result")


def model_to_api(model: dict[str, Any]) -> dict[str, Any]:
    """Convert AJP's portable note-type definition to AnkiConnect createModel args."""
    return {
        "modelName": model["name"],
        "inOrderFields": model["fields"],
        "css": model["css"],
        "isCloze": model["kind"] == "cloze",
        "cardTemplates": [
            {"Name": item["name"], "Front": item["question_html"], "Back": item["answer_html"]}
            for item in model["card_templates"]
        ],
    }


def existing_models_by_name(client: AnkiConnect) -> dict[str, dict[str, Any]]:
    model_map = client.call("modelNamesAndIds") or {}
    result: dict[str, dict[str, Any]] = {}
    model_ids = list(model_map.values())
    for start in range(0, len(model_ids), 50):
        batch = model_ids[start:start + 50]
        if not batch:
            continue
        for model in client.call("findModelsById", modelIds=batch) or []:
            name = model.get("name")
            if name:
                result[name] = model
    return result


def compare_model_spec(spec: dict[str, Any], raw_existing: dict[str, Any]) -> list[str]:
    """Return supported-signature incompatibilities for a same-named Anki model."""
    expected = model_signature_from_spec(spec)
    actual = model_signature_from_anki(raw_existing)
    problems: list[str] = []
    for key in ("name", "kind", "fields", "card_templates", "css"):
        if expected[key] != actual.get(key):
            problems.append(f"{key}: definición del paquete y definición de Anki no coinciden")
    if actual.get("unsupported_deck_overrides"):
        problems.append(
            "las plantillas existentes tienen sobrescrituras de mazo, que el protocolo 0.2.0 no puede declarar"
        )
    return problems


def load_pack(path: Path) -> dict[str, Any]:
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo leer el paquete {path}: {exc}") from exc
    errors = validate_pack(pack)
    if errors:
        formatted = "\n".join(f"  - {message}" for message in errors)
        raise RuntimeError(f"El paquete no pasa la validación AJP {CONTENT_PACK_VERSION}:\n{formatted}")
    return pack


def build_note_payload(note: dict[str, Any], model: dict[str, Any], deck_name: str) -> dict[str, Any]:
    values = note["fields"]
    field_map = {name: str(values.get(name, "")) for name in model["fields"]}
    tags = list(note["tags"])
    uid_tag = source_tag(note["source_id"])
    if uid_tag not in tags:
        tags.append(uid_tag)
    return {
        "deckName": deck_name,
        "modelName": model["name"],
        "fields": field_map,
        "tags": tags,
        "options": {
            "allowDuplicate": False,
            "duplicateScope": "collection",
            "duplicateScopeOptions": {"checkAllModels": False},
        },
    }


def source_id_already_exists(client: AnkiConnect, source_id: str) -> bool:
    return bool(client.call("findNotes", query=f"tag:{source_tag(source_id)}") or [])


def write_report(report_path: str | None, report: dict[str, Any]) -> None:
    if not report_path:
        return
    destination = Path(report_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", help="Archivo content_pack.json")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"AnkiConnect URL (predeterminado: {DEFAULT_URL})")
    parser.add_argument("--apply", action="store_true", help="Ejecuta las creaciones. Sin este indicador solo planifica.")
    parser.add_argument("--report-out", help="Ruta opcional para guardar un informe JSON del plan o la ejecución.")
    args = parser.parse_args()

    pack_path = Path(args.pack).expanduser().resolve()
    pack = load_pack(pack_path)
    client = AnkiConnect(args.url)

    api_version = client.call("version")
    if not isinstance(api_version, int) or api_version < API_VERSION:
        raise RuntimeError(f"Versión de AnkiConnect insuficiente o inesperada: {api_version!r}; se requiere API {API_VERSION}.")

    deck_map = client.call("deckNamesAndIds") or {}
    existing_decks = set(deck_map.keys())
    raw_models = existing_models_by_name(client)

    specs = pack["note_types"]
    spec_by_id = {item["type_id"]: item for item in specs}
    new_models: list[dict[str, Any]] = []
    reused_models: list[dict[str, Any]] = []
    new_decks = sorted(
        {item["name"] for item in pack["decks"]} - existing_decks,
        key=lambda name: (name.count("::"), name.casefold()),
    )
    conflicts: list[str] = []

    # Complete preflight of every declared model before any write can occur.
    for spec in specs:
        existing = raw_models.get(spec["name"])
        if existing is None:
            if spec["mode"] == "reuse":
                conflicts.append(f"El tipo de nota {spec['name']!r} está en modo reuse, pero no existe en esta colección.")
            else:
                new_models.append(spec)
            continue
        mismatches = compare_model_spec(spec, existing)
        if mismatches:
            conflicts.append(
                f"Conflicto con el tipo de nota existente {spec['name']!r}: " + "; ".join(mismatches) +
                ". No se actualizará. Usa otro nombre/versionado o corrige el paquete."
            )
        else:
            reused_models.append(spec)

    # Identify notes already imported from this protocol. This query is read-only.
    duplicate_source_ids: set[str] = set()
    for note in pack["notes"]:
        if source_id_already_exists(client, note["source_id"]):
            duplicate_source_ids.add(note["source_id"])

    if conflicts:
        formatted = "\n".join(f"  - {c}" for c in conflicts)
        raise RuntimeError("Preflight detenido: se encontraron conflictos antes de escribir cambios.\n" + formatted)

    print(f"Paquete: {pack['pack_id']} — {pack['metadata']['title']}")
    print(f"Protocolo: {CONTENT_PACK_VERSION} | AnkiConnect API: {API_VERSION} | servidor: {api_version}")
    print("Política: CREATE-ONLY; reutilización solo con definición compatible; nunca actualiza ni elimina.")
    print(f"Mazos: {len(pack['decks'])} declarados; {len(new_decks)} nuevos.")
    print(f"Tipos de nota: {len(specs)} declarados; {len(new_models)} nuevos; {len(reused_models)} reutilizados.")
    print(f"Notas declaradas: {len(pack['notes'])}; ya marcadas por source_id: {len(duplicate_source_ids)}.")
    print("\nPlan de mazos:")
    for deck in sorted({item["name"] for item in pack["decks"]}, key=lambda name: (name.count("::"), name.casefold())):
        print(f"  {'CREAR' if deck in new_decks else 'REUTILIZAR'} {deck}")
    print("Plan de tipos de nota:")
    for spec in specs:
        label = "CREAR" if spec in new_models else "REUTILIZAR SIN MODIFICAR"
        print(f"  {label}: {spec['name']} (tipo_id={spec['type_id']})")
    print("Plan de notas:")
    for note in pack["notes"]:
        status = "OMITIR: source_id ya importado" if note["source_id"] in duplicate_source_ids else "PROPONER CREACIÓN"
        print(f"  {status}: {note['source_id']} -> {note['deck']}")

    report: dict[str, Any] = {
        "protocol": "anki-json-protocol",
        "protocol_version": CONTENT_PACK_VERSION,
        "pack_id": pack["pack_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "plan",
        "api_version": api_version,
        "decks": {"declared": len(pack["decks"]), "created": [], "reused": []},
        "note_types": {"declared": len(specs), "created": [], "reused": []},
        "notes": {"declared": len(pack["notes"]), "created": [], "skipped_source_id": sorted(duplicate_source_ids), "skipped_duplicate": [], "failed": []},
        "apply_requested": bool(args.apply),
        "writes_attempted": False,
    }

    if not args.apply:
        print("\nModo de planificación: no se ha escrito ningún cambio. Añade --apply para ejecutar.")
        write_report(args.report_out, report)
        if args.report_out:
            print(f"Informe escrito: {Path(args.report_out).expanduser().resolve()}")
        return 0

    # Mutation phase; all structural/reference preflight checks have passed.
    # AnkiConnect has no multi-action transaction, so a transport/server failure mid-run
    # can leave previously created decks/models in place; no existing object is overwritten.
    try:
        for deck_name in new_decks:
            report["writes_attempted"] = True
            client.call("createDeck", deck=deck_name)
            report["decks"]["created"].append(deck_name)
            print(f"Creado mazo: {deck_name}")
        for deck in pack["decks"]:
            if deck["name"] not in new_decks:
                report["decks"]["reused"].append(deck["name"])

        for spec in new_models:
            report["writes_attempted"] = True
            client.call("createModel", **model_to_api(spec))
            report["note_types"]["created"].append(spec["name"])
            print(f"Creado tipo de nota: {spec['name']}")
        for spec in reused_models:
            report["note_types"]["reused"].append(spec["name"])
    except Exception as exc:
        report["fatal_error"] = f"Fallo durante la creación de estructura: {exc}"
        print(report["fatal_error"], file=sys.stderr)
        write_report(args.report_out, report)
        if args.report_out:
            print(f"Informe parcial escrito: {Path(args.report_out).expanduser().resolve()}")
        return 2

    for index, note in enumerate(pack["notes"], 1):
        source_id = note["source_id"]
        if source_id in duplicate_source_ids:
            continue
        model = spec_by_id[note["note_type_id"]]
        payload = build_note_payload(note, model, note["deck"])
        try:
            try:
                result = client.call("canAddNotesWithErrorDetail", notes=[payload]) or [{}]
                eligibility = result[0]
            except RuntimeError:
                legacy = client.call("canAddNotes", notes=[payload]) or [False]
                eligibility = {"canAdd": bool(legacy[0]), "error": "AnkiConnect indicó que no puede crear la nota."}
            if not eligibility.get("canAdd", False):
                msg = str(eligibility.get("error") or "Anki rechazó la nota, posiblemente por duplicidad.")
                report["notes"]["skipped_duplicate"].append({"source_id": source_id, "reason": msg})
                print(f"OMITIR nota {source_id}: {msg}")
                continue
            report["writes_attempted"] = True
            note_id = client.call("addNote", note=payload)
            if note_id is None:
                raise RuntimeError("AnkiConnect no devolvió un note_id.")
            report["notes"]["created"].append({"source_id": source_id, "note_id": note_id})
            print(f"Creada nota {index}/{len(pack['notes'])}: {source_id} (note_id={note_id})")
        except Exception as exc:
            report["notes"]["failed"].append({"source_id": source_id, "error": str(exc)})
            print(f"ERROR al crear {source_id}: {exc}", file=sys.stderr)

    print("\nResultado final:")
    print(f"  mazos creados: {len(report['decks']['created'])}")
    print(f"  tipos de nota creados: {len(report['note_types']['created'])}")
    print(f"  notas creadas: {len(report['notes']['created'])}")
    print(f"  omitidas por source_id: {len(report['notes']['skipped_source_id'])}")
    print(f"  omitidas por duplicado de Anki: {len(report['notes']['skipped_duplicate'])}")
    print(f"  fallos durante la escritura: {len(report['notes']['failed'])}")
    print("Anki genera las tarjetas a partir de las plantillas; el paquete no declara tarjetas individuales.")

    write_report(args.report_out, report)
    if args.report_out:
        print(f"Informe escrito: {Path(args.report_out).expanduser().resolve()}")
    return 0 if not report["notes"]["failed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

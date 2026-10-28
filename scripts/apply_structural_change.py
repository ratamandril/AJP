#!/usr/bin/env python3
"""Apply a guarded structural change by creating a parallel version and copying notes.

This does NOT edit the source note type in place. All writes are delegated to the
existing AJP v0.5+ apply_migration.py only after the current source model exactly
matches the contract embedded in the structural plan.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from plan_type_change import (
    AnkiConnect, EXPECTED_PROFILE, field_values, inspect_live, load_plan,
    map_fields, models_by_name, normalize_definition, source_tag, target_definition,
    target_source_id, to_migration_plan,
)


def save(path: str | None, data: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_source_cards(client: AnkiConnect, source_note_info: dict[str, Any]) -> list[dict[str, Any]]:
    cards = client.call("cardsInfo", cards=source_note_info.get("cards") or []) or []
    return [{k: c.get(k) for k in ("cardId", "note", "modelName", "deckName", "ord", "question", "answer")}
            for c in cards if isinstance(c, dict)]


def run_existing_migration(plan: dict[str, Any], url: str, confirm_profile: str, dry_run: bool) -> tuple[int, dict[str, Any], str, str]:
    root = Path(__file__).resolve().parents[1]
    migration_script = root / "scripts" / "apply_migration.py"
    if not migration_script.exists():
        raise RuntimeError("No se encontró scripts/apply_migration.py. Instala este parche en la raíz del proyecto AJP v0.5/v0.6.")
    with tempfile.TemporaryDirectory(prefix="ajp_structural_") as temp:
        temp_root = Path(temp)
        plan_path = temp_root / "migration_plan.json"
        report_path = temp_root / "migration_report.json"
        plan_path.write_text(json.dumps(to_migration_plan(plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [sys.executable, str(migration_script), str(plan_path), "--url", url, "--out", str(report_path)]
        if not dry_run:
            command += ["--apply", "--confirm-write", confirm_profile]
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        # Return the temporary report as an in-memory value before TemporaryDirectory is removed.
        return proc.returncode, report, proc.stdout, proc.stderr


def verify_result(client: AnkiConnect, plan: dict[str, Any], preflight: dict[str, Any], migration_report: dict[str, Any]) -> dict[str, Any]:
    source = plan["source"]
    target = plan["target_note_type"]
    expected_target = target_definition(target)
    current_models = models_by_name(client)
    source_model = current_models.get(source["anki_model_name"])
    target_model = current_models.get(target["anki_model_name"])
    if source_model is None or normalize_definition(source_model) != preflight["_source_definition"]:
        raise RuntimeError("La definición del tipo de origen cambió durante la operación.")
    if target_model is None or normalize_definition(target_model) != expected_target:
        raise RuntimeError("La definición del tipo destino no coincide con la especificación estructural.")

    note_results = []
    source_preservation = []
    source_by_id = {item["source_id"]: item for item in preflight["_source_preservation"]}
    for source_id in plan["selection"]["source_ids"]:
        old = source_by_id[source_id]
        old_info = client.call("notesInfo", notes=[old["source_note_id"]]) or []
        if len(old_info) != 1 or not isinstance(old_info[0], dict):
            raise RuntimeError(f"La nota fuente {source_id!r} ya no está disponible después de la migración.")
        after_source = old_info[0]
        source_fields_ok = field_values(after_source) == old["source_field_values"]
        source_tags_ok = list(after_source.get("tags") or []) == old["source_tags"]
        source_card_ids_ok = list(after_source.get("cards") or []) == old["source_card_ids"]
        source_cards_after = snapshot_source_cards(client, after_source)
        source_cards_ok = source_cards_after == old["source_card_details"]
        if not (source_fields_ok and source_tags_ok and source_card_ids_ok and source_cards_ok):
            raise RuntimeError(f"La nota fuente o sus tarjetas cambiaron durante la migración: {source_id!r}.")
        source_preservation.append({
            "source_id": source_id,
            "source_note_id": old["source_note_id"],
            "fields_unchanged": source_fields_ok,
            "tags_unchanged": source_tags_ok,
            "card_ids_unchanged": source_card_ids_ok,
            "card_rendering_and_deck_unchanged": source_cards_ok,
            "preserved": True,
        })

        new_source_id = target_source_id(source_id, target["type_id"], target["version"])
        new_ids = client.call("findNotes", query=f"tag:{source_tag(new_source_id)}") or []
        if len(new_ids) != 1:
            raise RuntimeError(f"Se esperaba una nota de destino única para {source_id!r}; se encontraron {len(new_ids)}.")
        target_infos = client.call("notesInfo", notes=new_ids) or []
        if len(target_infos) != 1 or not isinstance(target_infos[0], dict):
            raise RuntimeError(f"No se pudo leer la nota destino de {source_id!r}.")
        target_info = target_infos[0]
        expected_fields = map_fields(old["source_field_values"], plan)
        if target_info.get("modelName") != target["anki_model_name"]:
            raise RuntimeError(f"La nota destino {source_id!r} pertenece al tipo inesperado {target_info.get('modelName')!r}.")
        if field_values(target_info) != expected_fields:
            raise RuntimeError(f"Los campos destino de {source_id!r} no coinciden con el mapeo.")
        target_cards = client.call("cardsInfo", cards=target_info.get("cards") or []) or []
        if len(target_cards) != len(target["card_templates"]):
            raise RuntimeError(f"El destino {source_id!r} generó {len(target_cards)} tarjetas; el plan de prueba espera {len(target['card_templates'])}.")
        for card in target_cards:
            if card.get("deckName") != plan["target_deck"]:
                raise RuntimeError(f"Una tarjeta destino de {source_id!r} quedó en mazo incorrecto {card.get('deckName')!r}.")
            if not str(card.get("question", "")).strip() or not str(card.get("answer", "")).strip():
                raise RuntimeError(f"Una tarjeta destino de {source_id!r} tiene pregunta o respuesta vacía.")
            for field in ("Front", "Back", "Context", "Difficulty"):
                value = expected_fields.get(field, "")
                if value and field in ("Context", "Difficulty") and value not in str(card.get("answer", "")):
                    raise RuntimeError(f"La respuesta renderizada no contiene {field} en una tarjeta destino de {source_id!r}.")
        note_results.append({
            "source_id": source_id,
            "target_source_id": new_source_id,
            "source_note_id": old["source_note_id"],
            "target_note_id": target_info.get("noteId"),
            "target_fields": sorted(expected_fields),
            "target_card_count": len(target_cards),
            "target_template_names": [t["name"] for t in target["card_templates"]],
        })
    return {"source_preservation_checks": source_preservation, "target_notes_verified": note_results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="Structural change plan JSON")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--apply", action="store_true", help="Aplicar el versionado/copia; sin este flag solo planifica")
    parser.add_argument("--confirm-write", help="Debe ser AJP-SANDBOX para autorizar escrituras en ese perfil")
    parser.add_argument("--out", help="Informe JSON")
    args = parser.parse_args()

    try:
        plan = load_plan(Path(args.plan).expanduser().resolve())
        if args.apply and args.confirm_write != EXPECTED_PROFILE:
            raise RuntimeError(f"Escritura bloqueada: para aplicar indica --confirm-write {EXPECTED_PROFILE}.")
        client = AnkiConnect(args.url)
        preflight = inspect_live(client, plan)
        # Preserve exact source signature and source card renderings for post-write verification.
        src_model_raw = models_by_name(client)[plan["source"]["anki_model_name"]]
        preflight["_source_definition"] = normalize_definition(src_model_raw)
        for saved in preflight["_source_preservation"]:
            infos = client.call("notesInfo", notes=[saved["source_note_id"]]) or []
            if len(infos) != 1:
                raise RuntimeError(f"No se pudo volver a leer la nota fuente {saved['source_id']!r} antes de ejecutar.")
            saved["source_card_details"] = snapshot_source_cards(client, infos[0])

        public_report = {k: v for k, v in preflight.items() if not k.startswith("_")}
        public_report["mode"] = "apply" if args.apply else "plan"
        if not args.apply:
            print(f"PLAN VÁLIDO: {plan['plan_id']}")
            print("Cambio estructural versionado: se crearía una definición paralela; el origen permanece intacto.")
            print(f"Campos añadidos: {preflight['structural_diff']['fields_added']}")
            print(f"Plantillas modificadas: {preflight['structural_diff']['templates_changed']}")
            print("Modo plan-only: no se escribió nada en Anki.")
            save(args.out, public_report)
            return 0

        code, migration_report, stdout, stderr = run_existing_migration(plan, args.url, args.confirm_write, dry_run=False)
        public_report["migration_executor"] = migration_report
        public_report["migration_executor_stdout"] = stdout
        if stderr:
            public_report["migration_executor_stderr"] = stderr
        if code != 0:
            public_report["result"] = "partial_or_failed"
            public_report["migration_executor_exit_code"] = code
            save(args.out, public_report)
            print(f"Migración estructural detenida con código {code}; revisa el informe.", file=sys.stderr)
            return 2
        verified = verify_result(client, plan, preflight, migration_report)
        public_report.update(verified)
        public_report["writes_attempted"] = bool(migration_report.get("writes_attempted"))
        public_report["result"] = "passed"
        save(args.out, public_report)
        print(f"RESULTADO: PASSED | origen conservado; nueva versión {plan['target_note_type']['version']} verificada.")
        print(f"Informe: {Path(args.out).expanduser().resolve() if args.out else '(no guardado)'}")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

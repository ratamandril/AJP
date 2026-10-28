#!/usr/bin/env python3
"""Exercise AJP's copy-to-new-version migration against a disposable Anki profile."""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
API_VERSION = 6
DEFAULT_URL = "http://127.0.0.1:8765"
EXPECTED_PROFILE = "AJP-SANDBOX"
PLAN_PATH = ROOT / "examples" / "migration_plan.live_smoke.json"
SOURCE_ID = "org.openai.ajp.smoke.note.001"
SOURCE_MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.0.0]"
TARGET_MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.1.0]"
TARGET_DECK = "AJP-SANDBOX::Migration Test"
TARGET_TYPE_ID = "org.openai.anki-json-protocol.smoke.basic"
TARGET_VERSION = "1.1.0"

class AnkiConnect:
    def __init__(self, url: str, timeout: int = 60):
        self.url, self.timeout = url.rstrip("/"), timeout

    def call(self, action: str, **params: Any) -> Any:
        body = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo consultar AnkiConnect: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise RuntimeError(f"AnkiConnect action={action}: {payload.get('error') if isinstance(payload, dict) else 'respuesta inválida'}")
        return payload.get("result")


def source_tag(source_id: str) -> str:
    return "ajp_uid_" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]


def target_source_id(source_id: str) -> str:
    key = f"{source_id}\0{TARGET_TYPE_ID}@{TARGET_VERSION}".encode("utf-8")
    return "org.anki-json-protocol.migrated." + hashlib.sha256(key).hexdigest()[:40]


def get_one_note(client: AnkiConnect, source_id: str) -> dict[str, Any]:
    ids = client.call("findNotes", query=f"tag:{source_tag(source_id)}") or []
    if len(ids) != 1:
        raise RuntimeError(f"Se esperaba exactamente una nota para source_id {source_id!r}; se hallaron {len(ids)}.")
    notes = client.call("notesInfo", notes=ids) or []
    if len(notes) != 1 or not isinstance(notes[0], dict):
        raise RuntimeError("AnkiConnect no devolvió la nota esperada en notesInfo.")
    return notes[0]


def field_values(note: dict[str, Any]) -> dict[str, str]:
    return {k: str(v.get("value", "")) if isinstance(v, dict) else str(v)
            for k, v in (note.get("fields") or {}).items()}


def run_executor(url: str, out: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    command = [sys.executable, str(ROOT / "scripts" / "apply_migration.py"), str(PLAN_PATH),
               "--url", url, "--apply", "--confirm-write", EXPECTED_PROFILE, "--out", str(out)]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("Ejecutor de migración falló:\n" + result.stdout + "\n" + result.stderr)
    return result, json.loads(out.read_text(encoding="utf-8"))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--confirm-write", help=f"Debe ser exactamente {EXPECTED_PROFILE} para habilitar escrituras")
    parser.add_argument("--out", default="reports/live_migration_test.json")
    args = parser.parse_args()
    if args.confirm_write != EXPECTED_PROFILE:
        print(f"BLOQUEADO: ejecuta solo en el perfil de prueba y usa --confirm-write {EXPECTED_PROFILE}.", file=sys.stderr)
        return 2

    client = AnkiConnect(args.url)
    try:
        api_version = client.call("version")
        check(isinstance(api_version, int) and api_version >= API_VERSION, f"AnkiConnect API incompatible: {api_version!r}")
        profile = client.call("getActiveProfile")
        check(profile == EXPECTED_PROFILE, f"Perfil activo {profile!r}; se requiere {EXPECTED_PROFILE!r}.")
        source_before = get_one_note(client, SOURCE_ID)
        check(source_before.get("modelName") == SOURCE_MODEL, f"Tipo de origen inesperado: {source_before.get('modelName')!r}.")
        source_before_fields = field_values(source_before)
        source_before_tags = list(source_before.get("tags") or [])
        source_before_cards = list(source_before.get("cards") or [])
        expected_target_source_id = target_source_id(SOURCE_ID)

        with tempfile.TemporaryDirectory(prefix="ajp_migration_") as temp:
            first_out, second_out = Path(temp) / "first.json", Path(temp) / "second.json"
            first_run, first_report = run_executor(args.url, first_out)
            source_tag_target = source_tag(expected_target_source_id)
            target_ids = client.call("findNotes", query=f"tag:{source_tag_target}") or []
            check(len(target_ids) == 1, f"Después de migrar debe existir una nota destino; halladas: {len(target_ids)}.")
            target_notes = client.call("notesInfo", notes=target_ids) or []
            check(len(target_notes) == 1, "notesInfo no devolvió una sola nota destino.")
            target_note = target_notes[0]
            check(target_note.get("modelName") == TARGET_MODEL, "La nota destino usa un tipo de nota inesperado.")
            target_fields = field_values(target_note)
            check(target_fields.get("Front") == source_before_fields.get("Front"), "El campo Front no se copió correctamente.")
            check(target_fields.get("Back") == source_before_fields.get("Back"), "El campo Back no se copió correctamente.")
            check(target_fields.get("Context") == "Migrated by AJP integration test; synthetic fixture only.", "El campo Context no coincide con el mapeo.")
            target_card_ids = list(target_note.get("cards") or [])
            check(len(target_card_ids) == 2, f"El tipo destino debe generar 2 tarjetas; encontradas: {len(target_card_ids)}.")
            cards = client.call("cardsInfo", cards=target_card_ids) or []
            check(len(cards) == 2 and all(card.get("deckName") == TARGET_DECK for card in cards), "Las tarjetas destino no están en el mazo esperado.")

            # Verify the source is still present and byte-for-byte equivalent for managed note data.
            source_after = get_one_note(client, SOURCE_ID)
            check(source_after.get("noteId") == source_before.get("noteId"), "Cambió el ID de la nota de origen.")
            check(source_after.get("modelName") == SOURCE_MODEL, "Cambió el tipo de nota de origen.")
            check(field_values(source_after) == source_before_fields, "Se modificaron los campos de la nota de origen.")
            check(list(source_after.get("tags") or []) == source_before_tags, "Se modificaron las etiquetas de la nota de origen.")
            check(list(source_after.get("cards") or []) == source_before_cards, "Cambió la lista de tarjetas asociadas a la nota de origen.")

            # Run again; source_id derived from source/type/version must prevent duplicates.
            second_run, second_report = run_executor(args.url, second_out)
            target_ids_after = client.call("findNotes", query=f"tag:{source_tag_target}") or []
            check(target_ids_after == target_ids, "La segunda ejecución creó una nota duplicada o alteró el ID destino.")
            target_after = (client.call("notesInfo", notes=target_ids_after) or [None])[0]
            check(isinstance(target_after, dict) and list(target_after.get("cards") or []) == target_card_ids,
                  "La segunda ejecución cambió las tarjetas destino.")
            check(not second_report.get("notes", {}).get("created"), "La segunda ejecución no fue idempotente: intentó crear notas.")
            check(len(second_report.get("notes", {}).get("reused", [])) == 1,
                  "La segunda ejecución no registró la nota migrada como reutilizada.")

            result = {
                "protocol": "anki-json-migration-live-test",
                "test_version": "0.1.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "active_profile": profile,
                "api_version": api_version,
                "result": "passed",
                "checks": {
                    "profile_guard": "passed",
                    "source_note_found_by_source_id": "passed",
                    "target_type_and_deck_created_or_reused": "passed",
                    "field_mapping_copy_and_constant": "passed",
                    "two_target_cards_generated": "passed",
                    "source_note_fields_tags_and_cards_preserved": "passed",
                    "second_run_idempotence": "passed",
                },
                "artifacts": {
                    "source_id": SOURCE_ID,
                    "source_note_id": source_before.get("noteId"),
                    "source_model": SOURCE_MODEL,
                    "target_source_id": expected_target_source_id,
                    "target_note_id": target_note.get("noteId"),
                    "target_model": TARGET_MODEL,
                    "target_deck": TARGET_DECK,
                    "target_card_count": len(target_card_ids),
                    "original_note_preserved": True,
                    "cleanup": "No deletion performed. Inspect/remove only by deleting the disposable AJP-SANDBOX profile if desired."
                },
                "first_execution_report": first_report,
                "second_execution_report": second_report,
                "first_executor_stdout": first_run.stdout,
                "second_executor_stdout": second_run.stdout,
            }
        out = Path(args.out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("RESULTADO: PASSED")
        print("Migración por copia comprobada; origen conservado; 2 tarjetas destino; segunda ejecución idempotente.")
        print(f"Informe: {out}")
        return 0
    except Exception as exc:
        print(f"RESULTADO: FAILED — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

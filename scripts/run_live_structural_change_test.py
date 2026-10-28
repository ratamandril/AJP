#!/usr/bin/env python3
"""Integration test: compare a note type, create a parallel structural version, verify output.

Only writes when the active Anki profile is AJP-SANDBOX and --confirm-write matches.
The source note/type are intentionally preserved. Existing compatible @1.2.0 outputs
are reused, which lets this test be repeated without creating duplicate notes.
"""
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
PROFILE = "AJP-SANDBOX"
PLAN_PATH = ROOT / "examples" / "structural_change_plan.live_smoke.json"
SOURCE_ID = "org.anki-json-protocol.migrated.cb30b55ede13a42716cf99409eaa6e8618a5defb"
TYPE_ID = "org.openai.anki-json-protocol.smoke.basic"
TARGET_VERSION = "1.2.0"
SOURCE_MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.1.0]"
TARGET_MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.2.0]"
TARGET_DECK = "AJP-SANDBOX::Structural Test"
EXPECTED_DIFFICULTY = "SYNTHETIC TEST ONLY"


class AnkiConnect:
    def __init__(self, url: str, timeout: int = 60):
        self.url, self.timeout = url.rstrip("/"), timeout

    def call(self, action: str, **params: Any) -> Any:
        body = {"action": action, "version": API_VERSION}
        if params:
            body["params"] = params
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo contactar con AnkiConnect: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise RuntimeError(f"AnkiConnect action={action}: {payload.get('error') if isinstance(payload, dict) else 'respuesta inválida'}")
        return payload.get("result")


def source_tag(source_id: str) -> str:
    return "ajp_uid_" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]


def target_id(source_id: str) -> str:
    raw = f"{source_id}\0{TYPE_ID}@{TARGET_VERSION}".encode("utf-8")
    return "org.anki-json-protocol.migrated." + hashlib.sha256(raw).hexdigest()[:40]


def note_values(note: dict[str, Any]) -> dict[str, str]:
    return {k: str(v.get("value", "")) if isinstance(v, dict) else str(v)
            for k, v in (note.get("fields") or {}).items()}


def one_note(client: AnkiConnect, source_id: str, expected_model: str) -> dict[str, Any]:
    ids = client.call("findNotes", query=f"tag:{source_tag(source_id)}") or []
    if len(ids) != 1:
        raise RuntimeError(f"source_id {source_id!r}: se esperaba una nota, se encontraron {len(ids)}.")
    infos = client.call("notesInfo", notes=ids) or []
    if len(infos) != 1 or infos[0].get("modelName") != expected_model:
        actual = infos[0].get("modelName") if infos else None
        raise RuntimeError(f"Tipo de nota inesperado para {source_id!r}: {actual!r}.")
    return infos[0]


def card_state(client: AnkiConnect, note: dict[str, Any]) -> list[dict[str, Any]]:
    infos = client.call("cardsInfo", cards=note.get("cards") or []) or []
    return [{key: c.get(key) for key in ("cardId", "note", "modelName", "deckName", "ord", "question", "answer")}
            for c in infos]


def run_apply(url: str, out_path: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    script = ROOT / "scripts" / "apply_structural_change.py"
    cmd = [sys.executable, str(script), str(PLAN_PATH), "--url", url, "--apply", "--confirm-write", PROFILE, "--out", str(out_path)]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    report = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    if proc.returncode != 0:
        raise RuntimeError("La aplicación estructural falló:\n" + proc.stdout + "\n" + proc.stderr)
    return proc, report


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--confirm-write", help=f"Debe ser exactamente {PROFILE} para permitir escrituras")
    parser.add_argument("--out", default="reports/live_structural_change_test.json")
    args = parser.parse_args()
    if args.confirm_write != PROFILE:
        print(f"BLOQUEADO: esta prueba escribe. Usa --confirm-write {PROFILE} solo en el perfil desechable.", file=sys.stderr)
        return 2
    client = AnkiConnect(args.url)
    try:
        api = client.call("version")
        check(isinstance(api, int) and api >= API_VERSION, f"AnkiConnect API incompatible: {api!r}")
        profile = client.call("getActiveProfile")
        check(profile == PROFILE, f"Perfil activo {profile!r}; se requiere {PROFILE!r}.")
        source_before = one_note(client, SOURCE_ID, SOURCE_MODEL)
        source_fields_before = note_values(source_before)
        source_tags_before = list(source_before.get("tags") or [])
        source_card_ids_before = list(source_before.get("cards") or [])
        source_cards_before = card_state(client, source_before)
        out_path = target_id(SOURCE_ID)

        with tempfile.TemporaryDirectory(prefix="ajp_structural_test_") as temp:
            first_path, second_path = Path(temp) / "first.json", Path(temp) / "second.json"
            first_proc, first_report = run_apply(args.url, first_path)
            target_note = one_note(client, out_path, TARGET_MODEL)
            values = note_values(target_note)
            check(values.get("Front") == source_fields_before.get("Front"), "Front no se copió del origen.")
            check(values.get("Back") == source_fields_before.get("Back"), "Back no se copió del origen.")
            check(values.get("Context") == source_fields_before.get("Context"), "Context no se copió del origen.")
            check(values.get("Difficulty") == EXPECTED_DIFFICULTY, "Difficulty no se estableció según el plan.")
            target_cards = card_state(client, target_note)
            check(len(target_cards) == 2, f"Se esperaban 2 tarjetas nuevas; se encontraron {len(target_cards)}.")
            check(all(card["deckName"] == TARGET_DECK for card in target_cards), "Hay tarjetas destino en un mazo inesperado.")
            check(all(str(card.get("question", "")).strip() and str(card.get("answer", "")).strip() for card in target_cards),
                  "Una tarjeta destino tiene pregunta o respuesta vacía.")
            check(all(values["Context"] in card["answer"] and values["Difficulty"] in card["answer"] for card in target_cards),
                  "Las respuestas renderizadas no muestran Context y Difficulty.")

            # Check the old note and old rendered cards are unchanged.
            source_after = one_note(client, SOURCE_ID, SOURCE_MODEL)
            check(note_values(source_after) == source_fields_before, "Se modificaron campos de la nota origen.")
            check(list(source_after.get("tags") or []) == source_tags_before, "Se modificaron etiquetas de la nota origen.")
            check(list(source_after.get("cards") or []) == source_card_ids_before, "Cambió la lista de tarjetas del origen.")
            check(card_state(client, source_after) == source_cards_before, "Cambió la representación de las tarjetas originales.")

            second_proc, second_report = run_apply(args.url, second_path)
            target_ids_after = client.call("findNotes", query=f"tag:{source_tag(out_path)}") or []
            check(len(target_ids_after) == 1, "La segunda ejecución creó un duplicado de la nota destino.")
            target_after = (client.call("notesInfo", notes=target_ids_after) or [None])[0]
            check(target_after and target_after.get("noteId") == target_note.get("noteId"), "Cambió el ID de la nota destino en la segunda ejecución.")
            check(card_state(client, target_after) == target_cards, "La segunda ejecución cambió las tarjetas destino.")
            migration_notes = second_report.get("migration_executor", {}).get("notes", {})
            check(not migration_notes.get("created") and len(migration_notes.get("reused", [])) == 1,
                  "La segunda ejecución no reutilizó exactamente la nota destino existente.")

            report = {
                "protocol": "anki-json-structural-change-live-test",
                "test_version": "0.1.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "active_profile": profile,
                "api_version": api,
                "result": "passed",
                "checks": {
                    "source_definition_contract": "passed",
                    "structural_diff_detected": "passed",
                    "parallel_target_type_created_or_reused": "passed",
                    "new_field_mapped": "passed",
                    "changed_templates_render_expected_content": "passed",
                    "two_target_cards_generated": "passed",
                    "source_note_and_cards_preserved": "passed",
                    "second_run_idempotence": "passed"
                },
                "structural_diff": first_report.get("structural_diff"),
                "artifacts": {
                    "source_id": SOURCE_ID,
                    "source_note_id": source_before.get("noteId"),
                    "source_model": SOURCE_MODEL,
                    "source_cards_preserved": len(source_card_ids_before),
                    "target_source_id": out_path,
                    "target_note_id": target_note.get("noteId"),
                    "target_model": TARGET_MODEL,
                    "target_deck": TARGET_DECK,
                    "target_fields": sorted(values),
                    "target_cards": len(target_cards),
                    "cleanup": "No deletion performed; inspect/remove only by deleting the disposable AJP-SANDBOX profile if desired."
                },
                "first_execution_report": first_report,
                "second_execution_report": second_report,
                "first_stdout": first_proc.stdout,
                "second_stdout": second_proc.stdout
            }
        final_path = Path(args.out).expanduser().resolve()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("RESULTADO: PASSED")
        print("Se creó/reutilizó @1.2.0, se verificó Difficulty y las plantillas, se preservó @1.1.0 y la segunda ejecución fue idempotente.")
        print(f"Informe: {final_path}")
        return 0
    except Exception as exc:
        print(f"RESULTADO: FAILED — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

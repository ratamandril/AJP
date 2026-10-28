#!/usr/bin/env python3
"""Run a guarded, repeatable integration test against AnkiConnect.

Writes only test-prefixed objects and only if the active Anki profile is exactly
AJP-SANDBOX. It does not delete anything. Run against a dedicated disposable profile.
"""
from __future__ import annotations
import argparse
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
PACK_PATH = ROOT / "examples" / "content_pack.live_smoke.json"
SOURCE_ID = "org.openai.ajp.smoke.note.001"
MODEL_NAME = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.0.0]"
DECK_NAME = "AJP-SANDBOX::Smoke Test"

class AnkiConnect:
    def __init__(self, url: str, timeout: int = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def call(self, action: str, **params: Any) -> Any:
        request_body: dict[str, Any] = {"action": action, "version": API_VERSION}
        if params:
            request_body["params"] = params
        request = urllib.request.Request(
            self.url,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
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


def source_tag(source_id: str) -> str:
    import hashlib
    return "ajp_uid_" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]


def run_generator(pack_path: Path, report_out: Path, url: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable, str(ROOT / "scripts" / "apply_anki_pack.py"),
        str(pack_path), "--url", url, "--apply", "--report-out", str(report_out),
    ]
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def assert_check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_generated_cards(client: AnkiConnect, note_info: dict[str, Any], card_infos: list[dict[str, Any]]) -> list[str]:
    """Validate generated cards using AnkiConnect's modelTemplates action.

    `cardsInfo` does not include a `template` property. It returns rendered
    question/answer data and card metadata. Template names and HTML are read
    separately via `modelTemplates`.
    """
    assert_check(len(card_infos) == 2, "No se pudieron recuperar las dos tarjetas generadas.")

    template_definitions = client.call("modelTemplates", modelName=MODEL_NAME)
    assert_check(isinstance(template_definitions, dict), "AnkiConnect no devolvió las plantillas del tipo de nota.")

    expected_templates = {
        "Forward": {
            "Front": "<div class='ajp-smoke'>{{Front}}</div>",
            "Back": "{{FrontSide}}<hr id=answer><div>{{Back}}</div>",
        },
        "Reverse": {
            "Front": "<div class='ajp-smoke'>{{Back}}</div>",
            "Back": "{{FrontSide}}<hr id=answer><div>{{Front}}</div>",
        },
    }
    actual_template_names = set(template_definitions)
    expected_template_names = set(expected_templates)
    assert_check(
        actual_template_names == expected_template_names,
        f"Plantillas del tipo de nota inesperadas: {sorted(str(x) for x in actual_template_names)}",
    )
    for name, expected in expected_templates.items():
        actual = template_definitions.get(name)
        assert_check(isinstance(actual, dict), f"La definición de la plantilla {name!r} no es válida.")
        for side, expected_html in expected.items():
            assert_check(
                actual.get(side) == expected_html,
                f"HTML inesperado en {name}.{side}: {actual.get(side)!r}",
            )

    assert_check(all(card.get("modelName") == MODEL_NAME for card in card_infos),
                 "Una tarjeta no pertenece al tipo de nota de prueba esperado.")
    assert_check(all(card.get("deckName") == DECK_NAME for card in card_infos),
                 "Una tarjeta no está en el mazo de pruebas esperado.")
    questions = [str(card.get("question", "")) for card in card_infos]
    answers = [str(card.get("answer", "")) for card in card_infos]
    assert_check(all(q.strip() for q in questions), "Hay una pregunta renderizada vacía.")
    assert_check(all(a.strip() for a in answers), "Hay una respuesta renderizada vacía.")

    fields = note_info.get("fields", {})
    front_value = fields.get("Front", {}).get("value", "")
    back_value = fields.get("Back", {}).get("value", "")
    assert_check(bool(front_value) and bool(back_value), "Los valores Front/Back de la nota no están disponibles.")
    assert_check(sum(front_value in q for q in questions) == 1,
                 "No se encontró exactamente una pregunta generada desde el campo Front.")
    assert_check(sum(back_value in q for q in questions) == 1,
                 "No se encontró exactamente una pregunta generada desde el campo Back.")
    assert_check(all(front_value in a and back_value in a for a in answers),
                 "Las respuestas renderizadas no contienen los campos esperados.")

    # Return stable, meaningful template names without assuming cardsInfo has a `template` key.
    return sorted(actual_template_names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"URL local de AnkiConnect (predeterminado: {DEFAULT_URL})")
    parser.add_argument("--confirm-write", help=f"Debe ser exactamente {EXPECTED_PROFILE}; activa escrituras de prueba en ese perfil.")
    parser.add_argument("--out", default="reports/live_smoke_test.json", help="Ruta del informe JSON final.")
    args = parser.parse_args()

    if args.confirm_write != EXPECTED_PROFILE:
        print(
            "BLOQUEADO: este test escribe datos de prueba. Crea/abre un perfil desechable llamado "
            f"{EXPECTED_PROFILE!r} y vuelve a ejecutar con --confirm-write {EXPECTED_PROFILE}.",
            file=sys.stderr,
        )
        return 2

    client = AnkiConnect(args.url)
    try:
        api_version = client.call("version")
        if not isinstance(api_version, int) or api_version < API_VERSION:
            raise RuntimeError(f"AnkiConnect API incompatible: {api_version!r}; se necesita {API_VERSION}.")
        active_profile = client.call("getActiveProfile")
        assert_check(active_profile == EXPECTED_PROFILE,
                     f"Perfil activo {active_profile!r}; no se escribe. Abre {EXPECTED_PROFILE!r} en Anki.")

        with tempfile.TemporaryDirectory(prefix="ajp_smoke_") as temp:
            temp_dir = Path(temp)
            first_report = temp_dir / "first.json"
            second_report = temp_dir / "second.json"
            first = run_generator(PACK_PATH, first_report, args.url)
            if first.returncode != 0:
                raise RuntimeError("Primera ejecución del generador falló:\n" + first.stdout + "\n" + first.stderr)
            assert_check(first_report.exists(), "El generador no produjo el primer informe.")
            first_data = json.loads(first_report.read_text(encoding="utf-8"))

            active_profile_after = client.call("getActiveProfile")
            assert_check(active_profile_after == EXPECTED_PROFILE, "El perfil activo cambió durante la prueba.")
            note_ids = client.call("findNotes", query=f"tag:{source_tag(SOURCE_ID)}") or []
            assert_check(len(note_ids) == 1, f"Se esperaba exactamente una nota marcada; se encontraron {len(note_ids)}.")
            note_info = (client.call("notesInfo", notes=note_ids) or [None])[0]
            assert_check(isinstance(note_info, dict), "Anki no devolvió notesInfo para la nota de prueba.")
            assert_check(note_info.get("modelName") == MODEL_NAME,
                         f"Tipo de nota inesperado: {note_info.get('modelName')!r}.")
            assert_check(note_info.get("fields", {}).get("Front", {}).get("value") == "AJP smoke test: what does this verify?",
                         "El campo Front de la nota no coincide con el contenido de prueba.")
            assert_check(note_info.get("fields", {}).get("Back", {}).get("value"), "El campo Back aparece vacío.")
            card_ids = note_info.get("cards") or []
            assert_check(len(card_ids) == 2, f"Se esperaban dos tarjetas generadas por las dos plantillas; se encontraron {len(card_ids)}.")
            card_infos = client.call("cardsInfo", cards=card_ids) or []
            template_names = validate_generated_cards(client, note_info, card_infos)

            # The second apply verifies the generator's idempotence for the same source_id.
            second = run_generator(PACK_PATH, second_report, args.url)
            if second.returncode != 0:
                raise RuntimeError("Segunda ejecución del generador falló:\n" + second.stdout + "\n" + second.stderr)
            assert_check(second_report.exists(), "El generador no produjo el segundo informe.")
            second_data = json.loads(second_report.read_text(encoding="utf-8"))
            note_ids_after = client.call("findNotes", query=f"tag:{source_tag(SOURCE_ID)}") or []
            assert_check(note_ids_after == note_ids, "La segunda ejecución cambió el conjunto de notas de prueba.")
            note_info_after = (client.call("notesInfo", notes=note_ids_after) or [None])[0]
            assert_check(note_info_after and note_info_after.get("cards") == card_ids,
                         "La segunda ejecución modificó las tarjetas asociadas.")
            skipped = second_data.get("notes", {}).get("skipped_source_id", [])
            assert_check(SOURCE_ID in skipped, "El segundo informe no registra la omisión por source_id; idempotencia no demostrada.")

        result = {
            "protocol": "anki-json-protocol-live-smoke-test",
            "test_version": "0.1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "active_profile": active_profile,
            "api_version": api_version,
            "result": "passed",
            "checks": {
                "profile_guard": "passed",
                "create_deck_and_note_type": "passed",
                "create_note": "passed",
                "generate_two_cards": "passed",
                "modelTemplates_names_and_html": "passed",
                "rendered_questions_match_both_templates": "passed",
                "rendered_question_and_answer_nonempty": "passed",
                "second_run_idempotence": "passed",
                "existing_objects_not_updated_or_deleted": "consistent_with_create_only_generator; inspect console/reports for details"
            },
            "test_artifacts": {
                "deck": DECK_NAME,
                "note_type": MODEL_NAME,
                "source_id": SOURCE_ID,
                "note_count_for_source_id": 1,
                "generated_card_count": 2,
                "generated_template_names": template_names,
                "cleanup": "No deletion performed; close/delete the disposable AJP-SANDBOX profile after inspection if desired."
            },
            "first_generator_report": first_data,
            "second_generator_report": second_data,
            "generator_output_first": first.stdout,
            "generator_output_second": second.stdout
        }
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("RESULTADO: PASSED")
        print(f"Perfil verificado: {active_profile}")
        print(f"Nota: {SOURCE_ID}; plantillas: {', '.join(template_names)}; tarjetas: 2; segunda importación idempotente.")
        print(f"Informe: {out_path}")
        return 0
    except Exception as exc:
        print(f"RESULTADO: FAILED — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

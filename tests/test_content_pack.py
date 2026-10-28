from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ajp_core  # noqa: E402
import apply_anki_pack  # noqa: E402

PACK_PATH = ROOT / "examples" / "content_pack.fisica.json"


class ContentPackValidationTests(unittest.TestCase):
    def setUp(self):
        self.pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))

    def test_example_is_valid_against_semantic_rules(self):
        self.assertEqual(ajp_core.validate_pack(self.pack), [])

    def test_duplicate_source_id_is_rejected(self):
        self.pack["notes"].append(dict(self.pack["notes"][0]))
        errors = ajp_core.validate_pack(self.pack)
        self.assertTrue(any("source_id duplicado" in message for message in errors))

    def test_unknown_field_is_rejected(self):
        self.pack["notes"][0]["fields"]["Campo fantasma"] = "valor"
        errors = ajp_core.validate_pack(self.pack)
        self.assertTrue(any("campos que no pertenecen" in message for message in errors))

    def test_unknown_note_type_reference_is_rejected(self):
        self.pack["notes"][0]["note_type_id"] = "not.declared"
        errors = ajp_core.validate_pack(self.pack)
        self.assertTrue(any("no referencia un tipo de nota declarado" in message for message in errors))

    def test_missing_required_field_is_rejected(self):
        self.pack["notes"][0]["fields"]["Unidad SI"] = ""
        errors = ajp_core.validate_pack(self.pack)
        self.assertTrue(any("campos obligatorios" in message for message in errors))

    def test_unknown_template_field_is_rejected(self):
        self.pack["note_types"][0]["card_templates"][0]["question_html"] += "{{CampoFantasma}}"
        errors = ajp_core.validate_pack(self.pack)
        self.assertTrue(any("referencias a campos desconocidos" in message for message in errors))

    def test_duplicate_first_field_within_same_type_is_rejected(self):
        duplicate = dict(self.pack["notes"][0])
        duplicate["source_id"] = "fisica.test.duplicate"
        duplicate["fields"] = dict(duplicate["fields"])
        self.pack["notes"].append(duplicate)
        errors = ajp_core.validate_pack(self.pack)
        self.assertTrue(any("repite el primer campo" in message for message in errors))

    def test_cloze_note_requires_actual_cloze_deletion(self):
        cloze_pack = {
            "protocol": "anki-json-protocol", "protocol_version": "0.2.0", "pack_id": "demo.cloze",
            "metadata": {"title": "Cloze", "language": "es", "description": "test"},
            "decks": [{"name": "Demo"}],
            "note_types": [{
                "type_id": "demo.cloze.type", "version": "1.0.0", "name": "Demo Cloze",
                "mode": "ensure", "kind": "cloze", "fields": ["Texto"], "required_fields": ["Texto"],
                "card_templates": [{
                    "template_id": "demo.cloze.template", "name": "Cloze",
                    "question_html": "{{cloze:Texto}}", "answer_html": "{{cloze:Texto}}"
                }], "css": ""
            }],
            "notes": [{
                "source_id": "demo.cloze.note", "deck": "Demo", "note_type_id": "demo.cloze.type",
                "tags": [], "fields": {"Texto": "Una oración sin eliminación."}
            }]
        }
        errors = ajp_core.validate_pack(cloze_pack)
        self.assertTrue(any("tipo Cloze" in message for message in errors))
        cloze_pack["notes"][0]["fields"]["Texto"] = "La velocidad es {{c1::desplazamiento}} dividido por tiempo."
        self.assertEqual(ajp_core.validate_pack(cloze_pack), [])

    def test_schema_passes_example(self):
        import jsonschema
        schema = json.loads((ROOT / "schemas" / "content_pack.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(self.pack)

    def test_reuse_example_is_valid(self):
        reuse_path = ROOT / "examples" / "content_pack.reuse_existing_basic.json"
        reuse_pack = json.loads(reuse_path.read_text(encoding="utf-8"))
        self.assertEqual(ajp_core.validate_pack(reuse_pack), [])


class ModelCompatibilityTests(unittest.TestCase):
    def test_exact_supported_signature_matches(self):
        spec = {
            "name": "Demo", "kind": "standard", "fields": ["Front", "Back"],
            "css": ".card { color: black; }",
            "card_templates": [{"name": "Card 1", "question_html": "{{Front}}", "answer_html": "{{Back}}"}],
        }
        raw = {
            "name": "Demo", "type": 0, "css": ".card { color: black; }",
            "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
            "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}", "afmt": "{{Back}}", "did": None}],
        }
        self.assertEqual(apply_anki_pack.compare_model_spec(spec, raw), [])

    def test_template_difference_is_a_conflict(self):
        spec = {
            "name": "Demo", "kind": "standard", "fields": ["Front", "Back"],
            "css": ".card { color: black; }",
            "card_templates": [{"name": "Card 1", "question_html": "{{Front}}", "answer_html": "{{Back}}"}],
        }
        raw = {
            "name": "Demo", "type": 0, "css": ".card { color: black; }",
            "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
            "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}!", "afmt": "{{Back}}", "did": None}],
        }
        self.assertTrue(any("card_templates" in item for item in apply_anki_pack.compare_model_spec(spec, raw)))


class FakeReadOnlyClient:
    def __init__(self):
        self.actions = []

    def call(self, action, **params):
        self.actions.append(action)
        if action == "version":
            return 6
        if action == "deckNamesAndIds":
            return {"Predeterminado": 1}
        if action == "modelNamesAndIds":
            return {}
        if action == "findNotes":
            return []
        raise AssertionError(f"Unexpected API action during dry-run: {action}")


class ApplyPlanTests(unittest.TestCase):
    def test_dry_run_does_not_call_write_actions(self):
        client = FakeReadOnlyClient()
        with patch.object(apply_anki_pack, "AnkiConnect", return_value=client), \
             patch.object(sys, "argv", ["apply_anki_pack.py", str(PACK_PATH)]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = apply_anki_pack.main()
        self.assertEqual(result, 0)
        self.assertNotIn("createDeck", client.actions)
        self.assertNotIn("createModel", client.actions)
        self.assertNotIn("addNote", client.actions)
        self.assertIn("Modo de planificación", output.getvalue())


class FakeExistingTypeClient:
    def __init__(self, incompatible=False):
        self.actions = []
        self.incompatible = incompatible
        self.pack = json.loads((ROOT / "examples" / "content_pack.reuse_existing_basic.json").read_text(encoding="utf-8"))
        spec = self.pack["note_types"][0]
        self.raw_model = {
            "name": spec["name"], "type": 0,
            "css": spec["css"] + ("\n/* changed */" if incompatible else ""),
            "flds": [{"name": name, "ord": i} for i, name in enumerate(spec["fields"])],
            "tmpls": [
                {"name": item["name"], "ord": i, "qfmt": item["question_html"], "afmt": item["answer_html"], "did": None}
                for i, item in enumerate(spec["card_templates"])
            ],
        }

    def call(self, action, **params):
        self.actions.append(action)
        if action == "version": return 6
        if action == "deckNamesAndIds": return {"Predeterminado": 1}
        if action == "modelNamesAndIds": return {"Básico": 42}
        if action == "findModelsById": return [self.raw_model]
        if action == "findNotes": return []
        if action == "canAddNotesWithErrorDetail": return [{"canAdd": True}]
        if action == "addNote": return 7654321
        raise AssertionError(f"Unexpected Anki action: {action}")


class ApplyExecutionTests(unittest.TestCase):
    def test_reuse_mode_reuses_exact_model_and_adds_note(self):
        client = FakeExistingTypeClient()
        pack_path = ROOT / "examples" / "content_pack.reuse_existing_basic.json"
        with patch.object(apply_anki_pack, "AnkiConnect", return_value=client), \
             patch.object(sys, "argv", ["apply_anki_pack.py", str(pack_path), "--apply"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = apply_anki_pack.main()
        self.assertEqual(result, 0)
        self.assertIn("addNote", client.actions)
        self.assertNotIn("createModel", client.actions)
        self.assertNotIn("createDeck", client.actions)
        self.assertIn("REUTILIZAR SIN MODIFICAR", output.getvalue())

    def test_incompatible_existing_model_aborts_before_writes(self):
        client = FakeExistingTypeClient(incompatible=True)
        pack_path = ROOT / "examples" / "content_pack.reuse_existing_basic.json"
        with patch.object(apply_anki_pack, "AnkiConnect", return_value=client), \
             patch.object(sys, "argv", ["apply_anki_pack.py", str(pack_path), "--apply"]):
            with self.assertRaises(RuntimeError):
                apply_anki_pack.main()
        self.assertNotIn("createModel", client.actions)
        self.assertNotIn("createDeck", client.actions)
        self.assertNotIn("addNote", client.actions)


class FakeCreateOnlyClient:
    def __init__(self):
        self.actions = []
        self.created_model_specs = []
        self.added_notes = []
        self.note_counter = 2000

    def call(self, action, **params):
        self.actions.append(action)
        if action == "version": return 6
        if action == "deckNamesAndIds": return {"Predeterminado": 1}
        if action == "modelNamesAndIds": return {}
        if action == "findNotes": return []
        if action == "createDeck": return 99
        if action == "createModel":
            self.created_model_specs.append(params)
            return {"id": 7000 + len(self.created_model_specs)}
        if action == "canAddNotesWithErrorDetail": return [{"canAdd": True}]
        if action == "addNote":
            self.added_notes.append(params["note"])
            self.note_counter += 1
            return self.note_counter
        raise AssertionError(f"Unexpected Anki action: {action}")


class CreateOnlyExecutionTests(unittest.TestCase):
    def test_apply_creates_missing_types_and_notes(self):
        client = FakeCreateOnlyClient()
        pack_path = ROOT / "examples" / "content_pack.fisica.json"
        with patch.object(apply_anki_pack, "AnkiConnect", return_value=client), \
             patch.object(sys, "argv", ["apply_anki_pack.py", str(pack_path), "--apply"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = apply_anki_pack.main()
        self.assertEqual(result, 0)
        self.assertEqual(len(client.created_model_specs), 3)
        self.assertEqual(len(client.added_notes), 4)
        self.assertIn("createDeck", client.actions)
        self.assertEqual(client.actions.count("createModel"), 3)
        self.assertEqual(client.actions.count("addNote"), 4)
        self.assertNotIn("updateModelTemplates", client.actions)
        self.assertNotIn("updateNoteFields", client.actions)
        self.assertIn("notas creadas: 4", output.getvalue())


if __name__ == "__main__":
    unittest.main()

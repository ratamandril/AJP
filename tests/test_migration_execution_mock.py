from __future__ import annotations
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ajp_core  # noqa: E402
import apply_migration  # noqa: E402

SOURCE_ID = "org.openai.ajp.smoke.note.001"
SOURCE_MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.0.0]"
TARGET_MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.1.0]"
SOURCE_DECK = "AJP-SANDBOX::Smoke Test"
TARGET_DECK = "AJP-SANDBOX::Migration Test"


class FakeAnkiConnect:
    def __init__(self, incompatible_target: bool = False):
        self.next_note_id = 20
        self.next_card_id = 200
        self.next_model_id = 2
        self.write_actions: list[str] = []
        self.decks = {SOURCE_DECK: 1}
        self.models = {SOURCE_MODEL: self._model(SOURCE_MODEL, 1, ["Front", "Back"], [
            {"Name": "Forward", "Front": "<div class='ajp-smoke'>{{Front}}</div>", "Back": "{{FrontSide}}<hr id=answer><div>{{Back}}</div>"},
            {"Name": "Reverse", "Front": "<div class='ajp-smoke'>{{Back}}</div>", "Back": "{{FrontSide}}<hr id=answer><div>{{Front}}</div>"},
        ], ".card { font-family: Arial, sans-serif; font-size: 20px; text-align: center; line-height: 1.5; }\n.ajp-smoke { padding: 1em; }")}
        if incompatible_target:
            self.models[TARGET_MODEL] = self._model(TARGET_MODEL, 2, ["Front", "Back", "Context"], [
                {"Name": "Forward", "Front": "{{Front}}", "Back": "{{Back}}"},
                {"Name": "Reverse", "Front": "{{Back}}", "Back": "{{Front}}"},
            ], ".incompatible {}")
        source_fields = {"Front": "AJP smoke test: what does this verify?", "Back": "That the source remains unchanged."}
        self.notes = {
            10: {
                "noteId": 10, "modelName": SOURCE_MODEL,
                "fields": {k: {"value": v, "order": i} for i, (k, v) in enumerate(source_fields.items())},
                "tags": [ajp_core.source_tag(SOURCE_ID), "not_study_content"], "cards": [100, 101]
            }
        }
        self.cards = {
            100: {"cardId": 100, "nid": 10, "modelName": SOURCE_MODEL, "deckName": SOURCE_DECK,
                  "question": source_fields["Front"], "answer": source_fields["Back"]},
            101: {"cardId": 101, "nid": 10, "modelName": SOURCE_MODEL, "deckName": SOURCE_DECK,
                  "question": source_fields["Back"], "answer": source_fields["Front"]}
        }

    @staticmethod
    def _model(name, model_id, fields, templates, css):
        return {
            "id": model_id, "name": name, "type": 0, "sortf": 0, "css": css,
            "flds": [{"name": f, "ord": i} for i, f in enumerate(fields)],
            "tmpls": [{"name": t["Name"], "ord": i, "qfmt": t["Front"], "afmt": t["Back"], "did": None}
                      for i, t in enumerate(templates)]
        }

    def call(self, action, **params):
        if action == "version": return 6
        if action == "getActiveProfile": return "AJP-SANDBOX"
        if action == "deckNamesAndIds": return {name: i + 1 for i, name in enumerate(self.decks)}
        if action == "modelNamesAndIds": return {name: model["id"] for name, model in self.models.items()}
        if action == "findModelsById":
            wanted = set(params["modelIds"])
            return [copy.deepcopy(m) for m in self.models.values() if m["id"] in wanted]
        if action == "findNotes":
            query = params["query"]
            tag = query.removeprefix("tag:")
            return [nid for nid, note in self.notes.items() if tag in note["tags"]]
        if action == "notesInfo": return [copy.deepcopy(self.notes[nid]) for nid in params["notes"] if nid in self.notes]
        if action == "createDeck":
            self.write_actions.append(action)
            name = params["deck"]
            self.decks.setdefault(name, len(self.decks) + 1)
            return self.decks[name]
        if action == "createModel":
            self.write_actions.append(action)
            name = params["modelName"]
            model_id = self.next_model_id
            self.next_model_id += 1
            self.models[name] = self._model(name, model_id, params["inOrderFields"], params["cardTemplates"], params["css"])
            self.models[name]["type"] = 1 if params.get("isCloze") else 0
            return model_id
        if action in ("canAddNotesWithErrorDetail",):
            return [{"canAdd": True, "error": None} for _ in params["notes"]]
        if action == "canAddNotes": return [True for _ in params["notes"]]
        if action == "addNote":
            self.write_actions.append(action)
            payload = params["note"]
            nid = self.next_note_id
            self.next_note_id += 1
            model = self.models[payload["modelName"]]
            fields = {k: {"value": v, "order": i} for i, (k, v) in enumerate(payload["fields"].items())}
            card_ids = []
            for template in model["tmpls"]:
                cid = self.next_card_id
                self.next_card_id += 1
                card_ids.append(cid)
                q = template["qfmt"]
                a = template["afmt"]
                for fname, value in payload["fields"].items():
                    q = q.replace("{{" + fname + "}}", value)
                    a = a.replace("{{" + fname + "}}", value)
                a = a.replace("{{FrontSide}}", q)
                self.cards[cid] = {"cardId": cid, "nid": nid, "modelName": payload["modelName"],
                                   "deckName": payload["deckName"], "question": q, "answer": a}
            self.notes[nid] = {"noteId": nid, "modelName": payload["modelName"], "fields": fields,
                               "tags": list(payload["tags"]), "cards": card_ids}
            return nid
        if action == "findCards":
            nid = int(params["query"].split(":", 1)[1])
            return [cid for cid, card in self.cards.items() if card["nid"] == nid]
        if action == "cardsInfo": return [copy.deepcopy(self.cards[cid]) for cid in params["cards"] if cid in self.cards]
        raise RuntimeError(f"Fake action not implemented: {action}")


class MigrationExecutionMockTests(unittest.TestCase):
    def setUp(self):
        self.plan = ROOT / "examples" / "migration_plan.live_smoke.json"

    def run_main(self, client, out_path):
        argv = ["apply_migration.py", str(self.plan), "--apply", "--confirm-write", "AJP-SANDBOX", "--out", str(out_path)]
        with patch.object(sys, "argv", argv), patch.object(apply_migration, "AnkiConnect", return_value=client):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = apply_migration.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_apply_migrates_to_new_type_and_second_run_is_idempotent(self):
        client = FakeAnkiConnect()
        with tempfile.TemporaryDirectory() as temp:
            report1_path = Path(temp) / "first.json"
            code1, _, err1 = self.run_main(client, report1_path)
            self.assertEqual(code1, 0, err1)
            report1 = json.loads(report1_path.read_text(encoding="utf-8"))
            self.assertEqual(report1["result"], "passed")
            self.assertEqual(len(report1["notes"]["created"]), 1)
            self.assertEqual(report1["notes"]["created"][0]["generated_card_count"], 2)
            self.assertIn(TARGET_MODEL, client.models)
            self.assertIn(TARGET_DECK, client.decks)
            self.assertEqual(client.notes[10]["modelName"], SOURCE_MODEL)
            self.assertEqual(client.notes[10]["fields"]["Front"]["value"], "AJP smoke test: what does this verify?")

            report2_path = Path(temp) / "second.json"
            code2, _, err2 = self.run_main(client, report2_path)
            self.assertEqual(code2, 0, err2)
            report2 = json.loads(report2_path.read_text(encoding="utf-8"))
            self.assertEqual(report2["result"], "passed")
            self.assertEqual(report2["notes"]["created"], [])
            self.assertEqual(len(report2["notes"]["reused"]), 1)
            self.assertEqual(len([n for n in client.notes.values() if n["modelName"] == TARGET_MODEL]), 1)

    def test_incompatible_target_definition_aborts_before_writes(self):
        client = FakeAnkiConnect(incompatible_target=True)
        with tempfile.TemporaryDirectory() as temp:
            code, _, err = self.run_main(client, Path(temp) / "report.json")
            self.assertEqual(code, 2)
            self.assertIn("definición incompatible", err)
            self.assertEqual(client.write_actions, [])
            self.assertNotIn(TARGET_DECK, client.decks)


if __name__ == "__main__":
    unittest.main()

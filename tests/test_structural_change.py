from __future__ import annotations
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import plan_type_change  # noqa: E402
from structural_change_core import definition_diff, expected_definition, target_definition, validate_structural_plan, source_tag  # noqa: E402

PLAN_PATH = ROOT / "examples" / "structural_change_plan.live_smoke.json"


class FakeAnkiReadOnly:
    def __init__(self, plan, mutate_source=False, with_target=False):
        self.plan = plan
        self.actions = []
        src = plan["source"]
        definition = copy.deepcopy(src["expected_definition"])
        if mutate_source:
            definition["css"] += "\n/* unexpected drift */"
        self.source_model = {
            "id": 300, "name": src["anki_model_name"], "type": 1 if definition["kind"] == "cloze" else 0,
            "css": definition["css"],
            "flds": [{"name": name, "ord": i} for i, name in enumerate(definition["fields"])],
            "tmpls": [{"name": t["name"], "ord": i, "qfmt": t["question_html"], "afmt": t["answer_html"], "did": None}
                      for i, t in enumerate(definition["card_templates"])],
        }
        self.models = {src["anki_model_name"]: self.source_model}
        if with_target:
            target = plan["target_note_type"]
            self.models[target["anki_model_name"]] = {
                "id": 301, "name": target["anki_model_name"], "type": 1 if target["kind"] == "cloze" else 0,
                "css": target["css"],
                "flds": [{"name": name, "ord": i} for i, name in enumerate(target["fields"])],
                "tmpls": [{"name": t["name"], "ord": i, "qfmt": t["question_html"], "afmt": t["answer_html"], "did": None}
                          for i, t in enumerate(target["card_templates"])],
            }
        source_id = plan["selection"]["source_ids"][0]
        self.note = {
            "noteId": 400,
            "modelName": src["anki_model_name"],
            "fields": {
                "Front": {"value": "Synthetic front", "order": 0},
                "Back": {"value": "Synthetic back", "order": 1},
                "Context": {"value": "Migrated by AJP integration test; controlled update test v0.6.0; synthetic fixture only.", "order": 2},
            },
            "tags": [source_tag(source_id), "not_study_content"],
            "cards": [500, 501],
        }
        self.cards = [
            {"cardId": 500, "note": 400, "modelName": src["anki_model_name"], "deckName": "AJP-SANDBOX::Migration Test", "ord": 0, "question": "Synthetic front", "answer": "Synthetic front<hr>Synthetic back"},
            {"cardId": 501, "note": 400, "modelName": src["anki_model_name"], "deckName": "AJP-SANDBOX::Migration Test", "ord": 1, "question": "Synthetic back", "answer": "Synthetic back<hr>Synthetic front"},
        ]
        self.target_note = None

    def call(self, action, **params):
        self.actions.append(action)
        if action == "version": return 6
        if action == "getActiveProfile": return "AJP-SANDBOX"
        if action == "modelNamesAndIds": return {name: model["id"] for name, model in self.models.items()}
        if action == "findModelsById":
            wanted = set(params["modelIds"])
            return [copy.deepcopy(m) for m in self.models.values() if m["id"] in wanted]
        if action == "deckNamesAndIds": return {"AJP-SANDBOX::Migration Test": 40}
        if action == "findNotes":
            query = params["query"]
            tag = query.removeprefix("tag:")
            if tag in self.note["tags"]: return [400]
            if self.target_note and tag in self.target_note["tags"]: return [401]
            return []
        if action == "notesInfo":
            result = []
            for nid in params["notes"]:
                if nid == 400: result.append(copy.deepcopy(self.note))
                elif nid == 401 and self.target_note: result.append(copy.deepcopy(self.target_note))
            return result
        if action == "cardsInfo":
            ids = params["cards"]
            return [copy.deepcopy(c) for c in self.cards if c["cardId"] in ids]
        raise AssertionError(f"Unexpected action in read-only test: {action}")


class StructuralPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    def test_example_plan_semantically_valid(self):
        self.assertEqual(validate_structural_plan(self.plan), [])

    def test_json_schema_accepts_example(self):
        import jsonschema
        schema = json.loads((ROOT / "schemas" / "structural_change_plan.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(self.plan)

    def test_diff_finds_added_field_changed_templates_and_css(self):
        diff = definition_diff(expected_definition(self.plan["source"]), target_definition(self.plan["target_note_type"]))
        self.assertEqual(diff["fields_added"], ["Difficulty"])
        self.assertEqual(diff["fields_removed"], [])
        self.assertEqual({x["name"] for x in diff["templates_changed"]}, {"Forward", "Reverse"})
        self.assertTrue(diff["css_changed"])
        self.assertEqual(diff["target_template_count"], 2)

    def test_plan_requires_a_versioned_parallel_model(self):
        self.plan["target_note_type"]["anki_model_name"] = self.plan["source"]["anki_model_name"]
        self.assertTrue(any("debe ser distinto" in e for e in validate_structural_plan(self.plan)))

    def test_plan_requires_mapping_for_each_target_field(self):
        self.plan["field_mapping"].pop()
        self.assertTrue(any("cubrir exactamente" in e for e in validate_structural_plan(self.plan)))

    def test_plan_rejects_unknown_template_field(self):
        self.plan["target_note_type"]["card_templates"][0]["answer_html"] += "{{UnknownField}}"
        self.assertTrue(any("campos desconocidos" in e for e in validate_structural_plan(self.plan)))

    def test_live_preflight_accepts_expected_source_and_never_writes(self):
        client = FakeAnkiReadOnly(self.plan)
        report = plan_type_change.inspect_live(client, self.plan)
        self.assertFalse(report["writes_attempted"])
        self.assertEqual(report["structural_diff"]["fields_added"], ["Difficulty"])
        self.assertEqual(report["notes"][0]["action"], "create")
        self.assertNotIn("createDeck", client.actions)
        self.assertNotIn("createModel", client.actions)
        self.assertNotIn("addNote", client.actions)

    def test_live_preflight_aborts_if_source_definition_drifted(self):
        client = FakeAnkiReadOnly(self.plan, mutate_source=True)
        with self.assertRaisesRegex(RuntimeError, "no coincide con source.expected_definition"):
            plan_type_change.inspect_live(client, self.plan)

    def test_public_report_matches_report_schema(self):
        import jsonschema
        client = FakeAnkiReadOnly(self.plan)
        report = plan_type_change.inspect_live(client, self.plan)
        public_report = {k: v for k, v in report.items() if not k.startswith("_")}
        schema = json.loads((ROOT / "schemas" / "structural_change_report.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(public_report)

    def test_target_source_id_is_deterministic(self):
        a = plan_type_change.target_source_id("source-a", "type-a", "1.2.0")
        b = plan_type_change.target_source_id("source-a", "type-a", "1.2.0")
        self.assertEqual(a, b)
        self.assertNotEqual(a, plan_type_change.target_source_id("source-a", "type-a", "1.3.0"))


if __name__ == "__main__":
    unittest.main()

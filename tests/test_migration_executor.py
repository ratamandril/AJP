from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_migration  # noqa: E402
import plan_migration  # noqa: E402


class MigrationExecutorUnitTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "examples" / "migration_plan.live_smoke.json").read_text(encoding="utf-8"))

    def test_live_fixture_plan_is_valid(self):
        errors, summary = plan_migration.validate_plan(self.plan)
        self.assertEqual(errors, [])
        self.assertEqual(summary["target_deck"], "AJP-SANDBOX::Migration Test")

    def test_migrated_source_id_is_stable_for_same_source_and_version(self):
        a = apply_migration.deterministic_migrated_source_id("source-a", "type.family", "1.1.0")
        b = apply_migration.deterministic_migrated_source_id("source-a", "type.family", "1.1.0")
        self.assertEqual(a, b)
        self.assertNotEqual(a, apply_migration.deterministic_migrated_source_id("source-b", "type.family", "1.1.0"))
        self.assertNotEqual(a, apply_migration.deterministic_migrated_source_id("source-a", "type.family", "1.2.0"))

    def test_field_mapping_copy_constant_and_empty(self):
        plan = dict(self.plan)
        plan["target_note_type"] = dict(self.plan["target_note_type"])
        plan["target_note_type"]["fields"] = ["Front", "Back", "Context", "Extra"]
        plan["field_mapping"] = [
            {"target_field": "Front", "operation": "copy", "source_field": "Front"},
            {"target_field": "Back", "operation": "copy", "source_field": "Back"},
            {"target_field": "Context", "operation": "constant", "value": "migrated"},
            {"target_field": "Extra", "operation": "empty"}
        ]
        out = apply_migration.mapped_fields({"Front": "Q", "Back": "A"}, plan)
        self.assertEqual(out, {"Front": "Q", "Back": "A", "Context": "migrated", "Extra": ""})

    def test_missing_source_field_rejected(self):
        with self.assertRaises(RuntimeError):
            apply_migration.mapped_fields({"Front": "Q"}, self.plan)

    def test_target_definition_converts_to_anki_model_shape(self):
        spec = apply_migration.model_spec_for_target(self.plan["target_note_type"])
        self.assertEqual(spec["name"], self.plan["target_note_type"]["anki_model_name"])
        self.assertEqual(spec["fields"], ["Front", "Back", "Context"])
        api = apply_migration.model_to_api(spec)
        self.assertEqual(api["modelName"], spec["name"])
        self.assertEqual([t["Name"] for t in api["cardTemplates"]], ["Forward", "Reverse"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import plan_migration  # noqa: E402


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "examples" / "migration_plan.physics_magnitude_1_0_to_1_1.json").read_text(encoding="utf-8"))

    def test_sample_plan_is_valid_without_collection_snapshot(self):
        errors, summary = plan_migration.validate_plan(self.plan)
        self.assertEqual(errors, [])
        self.assertEqual(summary["execution"].split(" — ")[0], "PLAN_ONLY")
        self.assertEqual(summary["field_mapping_count"], 6)

    def test_target_must_be_higher_version(self):
        self.plan["target_note_type"]["version"] = "1.0.0"
        self.plan["target_note_type"]["anki_model_name"] = "AJP::org.estudio.fisica.magnitud@1.0.0"
        errors, _ = plan_migration.validate_plan(self.plan)
        self.assertTrue(any("mayor que la versión de origen" in error for error in errors))

    def test_each_target_field_must_be_mapped_once(self):
        self.plan["field_mapping"].pop()
        errors, _ = plan_migration.validate_plan(self.plan)
        self.assertTrue(any("faltan" in error for error in errors))

    def test_target_type_name_must_be_versioned(self):
        self.plan["target_note_type"]["anki_model_name"] = "Física - Magnitud nueva"
        errors, _ = plan_migration.validate_plan(self.plan)
        self.assertTrue(any("marcador @" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

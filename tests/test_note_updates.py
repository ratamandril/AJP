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
import apply_note_updates  # noqa: E402

SOURCE_ID = "org.anki-json-protocol.migrated.cb30b55ede13a42716cf99409eaa6e8618a5defb"
MODEL = "AJP-SANDBOX - Smoke Basic [AJP:org.openai.anki-json-protocol.smoke.basic@1.1.0]"
ORIGINAL = "Migrated by AJP integration test; synthetic fixture only."
DESIRED = "Migrated by AJP integration test; controlled update test v0.6.0; synthetic fixture only."


class FakeAnki:
    def __init__(self, current=ORIGINAL, model=MODEL, profile="AJP-SANDBOX", duplicate=False):
        self.profile = profile
        self.note_id = 73
        self.fields = {"Front": "synthetic front", "Back": "synthetic back", "Context": current}
        tags = [ajp_core.source_tag(SOURCE_ID), "not_study_content"]
        self.notes = {
            self.note_id: {"noteId": self.note_id, "modelName": model,
                              "fields": {k: {"value": v, "order": i} for i, (k, v) in enumerate(self.fields.items())},
                              "tags": tags, "cards": [801, 802]}
        }
        if duplicate:
            self.notes[74] = copy.deepcopy(self.notes[self.note_id])
            self.notes[74]["noteId"] = 74
        self.writes = []

    def call(self, action, **params):
        if action == "version": return 6
        if action == "getActiveProfile": return self.profile
        if action == "findNotes":
            tag = params["query"].removeprefix("tag:")
            return [nid for nid, n in self.notes.items() if tag in n["tags"]]
        if action == "notesInfo": return [copy.deepcopy(self.notes[nid]) for nid in params["notes"] if nid in self.notes]
        if action == "updateNoteFields":
            self.writes.append((action, copy.deepcopy(params["note"])))
            nid = params["note"]["id"]
            for field, value in params["note"]["fields"].items():
                self.notes[nid]["fields"][field]["value"] = value
                self.fields[field] = value
            return None
        raise RuntimeError(f"API inesperada en mock: {action}")


def pack():
    return {
        "protocol": "anki-json-note-update-pack", "protocol_version": "0.1.0", "pack_id": "test.update.001",
        "metadata": {"title": "test", "description": "test fixture"},
        "updates": [{"source_id": SOURCE_ID, "expected_model_name": MODEL,
                      "expected_fields": {"Context": ORIGINAL}, "set_fields": {"Context": DESIRED}}]
    }


class NoteUpdateTests(unittest.TestCase):
    def run_main(self, client, apply=False, confirm="AJP-SANDBOX", data=None):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pack.json"
            path.write_text(json.dumps(data or pack()), encoding="utf-8")
            report = Path(td) / "report.json"
            argv = ["apply_note_updates.py", str(path), "--report-out", str(report)]
            if apply:
                argv += ["--apply", "--confirm-profile", confirm]
            with patch.object(sys, "argv", argv), patch.object(apply_note_updates, "AnkiConnect", return_value=client):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = apply_note_updates.main()
            parsed = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
            return code, parsed, stdout.getvalue(), stderr.getvalue()

    def test_plan_is_read_only(self):
        c = FakeAnki()
        code, r, _, _ = self.run_main(c)
        self.assertEqual(code, 0)
        self.assertFalse(r["writes_attempted"])
        self.assertEqual(c.writes, [])
        self.assertEqual(r["updates"][0]["status"], "update")

    def test_apply_and_rerun_are_idempotent(self):
        c = FakeAnki()
        code1, r1, _, err1 = self.run_main(c, apply=True)
        self.assertEqual(code1, 0, err1)
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(c.notes[73]["fields"]["Context"]["value"], DESIRED)
        self.assertEqual(c.notes[73]["fields"]["Front"]["value"], "synthetic front")
        code2, r2, _, err2 = self.run_main(c, apply=True)
        self.assertEqual(code2, 0, err2)
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(r2["updates"][0]["status"], "already_applied")

    def test_stale_precondition_aborts_before_any_writes(self):
        c = FakeAnki(current="someone edited this")
        code, r, _, _ = self.run_main(c, apply=True)
        self.assertEqual(code, 2)
        self.assertEqual(c.writes, [])
        self.assertEqual(len(r["preflight_conflicts"]), 1)

    def test_wrong_model_aborts(self):
        c = FakeAnki(model="Básico")
        code, r, _, _ = self.run_main(c, apply=True)
        self.assertEqual(code, 2)
        self.assertEqual(c.writes, [])

    def test_wrong_profile_never_writes(self):
        c = FakeAnki(profile="Personal")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pack.json"
            path.write_text(json.dumps(pack()), encoding="utf-8")
            argv = ["apply_note_updates.py", str(path), "--apply", "--confirm-profile", "AJP-SANDBOX"]
            with patch.object(sys, "argv", argv), patch.object(apply_note_updates, "AnkiConnect", return_value=c):
                with self.assertRaisesRegex(RuntimeError, "Escritura bloqueada"):
                    apply_note_updates.main()
        self.assertEqual(c.writes, [])

    def test_duplicate_source_marker_is_conflict(self):
        c = FakeAnki(duplicate=True)
        code, r, _, _ = self.run_main(c, apply=True)
        self.assertEqual(code, 2)
        self.assertEqual(c.writes, [])
        self.assertEqual(r["updates"][0]["status"], "conflict")

    def test_unowned_note_cannot_be_found_by_ajp_source_marker(self):
        c = FakeAnki()
        c.notes[73]["tags"] = ["not_study_content"]
        code, r, _, _ = self.run_main(c, apply=True)
        self.assertEqual(code, 2)
        self.assertEqual(c.writes, [])

    def test_invalid_pack_requires_expected_value_for_each_mutated_field(self):
        p = pack()
        p["updates"][0]["expected_fields"] = {"Front": "synthetic front"}
        self.assertTrue(any("precondiciones para todos los campos" in e for e in apply_note_updates.validate_update_pack(p)))


if __name__ == "__main__":
    unittest.main()

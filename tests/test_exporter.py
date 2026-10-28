from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import export_anki_snapshot as exporter  # noqa: E402

MODEL = {
    "id": 100,
    "name": "Basic",
    "type": 0,
    "mod": 1700000000,
    "sortf": 0,
    "did": None,
    "flds": [
        {"name": "Front", "ord": 0, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "description": "", "plainText": False, "collapsed": False, "excludeFromSearch": False, "id": 111, "tag": None, "preventDeletion": False},
        {"name": "Back", "ord": 1, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "description": "", "plainText": False, "collapsed": False, "excludeFromSearch": False, "id": 112, "tag": None, "preventDeletion": False},
    ],
    "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr>{{Back}}", "bqfmt": "", "bafmt": "", "did": None, "bfont": "", "bsize": 0, "id": 222}],
    "css": ".card { font-size: 20px; }",
    "req": [[0, "any", [0]]],
    "latexPre": "",
    "latexPost": "",
}


class FakeAnkiHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        action = body["action"]
        params = body.get("params", {})
        if action == "version":
            result = 6
        elif action == "deckNamesAndIds":
            result = {"Default": 1, "Física::MRU": 2}
        elif action == "modelNamesAndIds":
            result = {"Basic": 100}
        elif action == "findModelsById":
            result = [MODEL]
        elif action == "modelFieldsOnTemplates":
            result = {"Card 1": [["Front"], ["Back"]]}
        elif action == "findNotes":
            result = [1000]
        elif action == "findCards":
            result = [2000]
        elif action == "notesInfo":
            result = [{
                "noteId": 1000,
                "modelName": "Basic",
                "tags": ["test", "fisica"],
                "fields": {"Front": {"value": "¿Qué es MRU?", "order": 0}, "Back": {"value": "Movimiento rectilíneo uniforme", "order": 1}},
                "mod": 1700000000,
                "profile": "TestProfile",
                "cards": [2000],
            }]
        elif action == "cardsInfo":
            result = [{
                "cardId": 2000,
                "note": 1000,
                "modelName": "Basic",
                "deckName": "Física::MRU",
                "ord": 0,
                "fieldOrder": 0,
                "question": "¿Qué es MRU?",
                "answer": "Movimiento rectilíneo uniforme",
                "mod": 1700000000,
                "type": 2,
                "queue": 2,
                "due": 123,
                "ivl": 3,
                "reps": 5,
                "lapses": 1,
            }]
        elif action == "getTags":
            result = ["fisica", "test"]
        elif action == "getMediaFilesNames":
            result = ["mru.png"]
        elif action == "getDeckConfig":
            result = {"id": 1, "name": "Default", "dyn": False}
        else:
            self.send_response(400)
            self.end_headers()
            return
        response = json.dumps({"result": result, "error": None}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class ExporterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAnkiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def run_export(self, *arguments):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "snapshot.json"
            argv = ["export_anki_snapshot.py", "--url", self.url, "--out", str(output), *arguments]
            with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(exporter.main(), 0)
            return json.loads(output.read_text(encoding="utf-8"))

    def test_full_snapshot_has_model_note_and_card_relationships(self):
        snapshot = self.run_export("--include-scheduling", "--include-media-inventory")
        self.assertEqual(snapshot["snapshot_schema_version"], "0.2.0")
        self.assertEqual(snapshot["summary"]["note_count_in_collection"], 1)
        self.assertEqual(snapshot["summary"]["card_count_in_collection"], 1)
        self.assertEqual(snapshot["note_types"][0]["fields_on_templates"]["Card 1"], [["Front"], ["Back"]])
        self.assertEqual(snapshot["cards"][0]["template_name"], "Card 1")
        self.assertEqual(snapshot["cards"][0]["ivl"], 3)
        self.assertEqual(snapshot["tags"], ["fisica", "test"])
        self.assertEqual(snapshot["media_inventory"], ["mru.png"])
        self.assertEqual(snapshot["decks"][1]["parent_name"], "Física")

    def test_schema_only_keeps_counts_and_omits_note_and_card_content(self):
        snapshot = self.run_export("--schema-only")
        self.assertEqual(snapshot["summary"]["note_count_in_collection"], 1)
        self.assertEqual(snapshot["summary"]["card_count_in_collection"], 1)
        self.assertEqual(snapshot["notes"], [])
        self.assertEqual(snapshot["cards"], [])
        self.assertFalse(snapshot["sections"]["notes"]["included"])
        self.assertFalse(snapshot["source"]["local_collection_content_written_to_snapshot"])


if __name__ == "__main__":
    unittest.main()

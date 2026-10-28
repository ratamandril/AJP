from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_live_smoke_test  # noqa: E402


class LiveSmokeTemplateTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.call.return_value = {
            "Forward": {
                "Front": "<div class='ajp-smoke'>{{Front}}</div>",
                "Back": "{{FrontSide}}<hr id=answer><div>{{Back}}</div>",
            },
            "Reverse": {
                "Front": "<div class='ajp-smoke'>{{Back}}</div>",
                "Back": "{{FrontSide}}<hr id=answer><div>{{Front}}</div>",
            },
        }
        self.note_info = {
            "fields": {
                "Front": {"value": "AJP smoke test: what does this verify?"},
                "Back": {"value": "That AnkiConnect can create a note and Anki generates its two card templates. This is disposable test content."},
            }
        }
        # Intentionally no `template` key: real cardsInfo doesn't provide it.
        self.card_infos = [
            {
                "modelName": run_live_smoke_test.MODEL_NAME,
                "deckName": run_live_smoke_test.DECK_NAME,
                "question": "<div class='ajp-smoke'>AJP smoke test: what does this verify?</div>",
                "answer": "<div class='ajp-smoke'>AJP smoke test: what does this verify?</div><hr id=answer><div>That AnkiConnect can create a note and Anki generates its two card templates. This is disposable test content.</div>",
            },
            {
                "modelName": run_live_smoke_test.MODEL_NAME,
                "deckName": run_live_smoke_test.DECK_NAME,
                "question": "<div class='ajp-smoke'>That AnkiConnect can create a note and Anki generates its two card templates. This is disposable test content.</div>",
                "answer": "<div class='ajp-smoke'>That AnkiConnect can create a note and Anki generates its two card templates. This is disposable test content.</div><hr id=answer><div>AJP smoke test: what does this verify?</div>",
            },
        ]

    def test_uses_model_templates_not_nonexistent_cardsinfo_template_key(self):
        names = run_live_smoke_test.validate_generated_cards(self.client, self.note_info, self.card_infos)
        self.assertEqual(names, ["Forward", "Reverse"])
        self.client.call.assert_called_once_with("modelTemplates", modelName=run_live_smoke_test.MODEL_NAME)

    def test_rejects_wrong_template_names(self):
        self.client.call.return_value = {"Card 1": {"Front": "x", "Back": "y"}}
        with self.assertRaisesRegex(RuntimeError, "Plantillas del tipo de nota inesperadas"):
            run_live_smoke_test.validate_generated_cards(self.client, self.note_info, self.card_infos)

    def test_rejects_question_not_rendered_from_both_fields(self):
        altered = [dict(self.card_infos[0]), dict(self.card_infos[1])]
        altered[1]["question"] = "<div>wrong question</div>"
        with self.assertRaisesRegex(RuntimeError, "campo Back"):
            run_live_smoke_test.validate_generated_cards(self.client, self.note_info, altered)


if __name__ == "__main__":
    unittest.main()

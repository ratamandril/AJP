from __future__ import annotations
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_live_structural_change_test  # noqa: E402

class StructuralChangeGuardTests(unittest.TestCase):
    def test_wrong_confirmation_is_blocked_before_network_access(self):
        with patch.object(run_live_structural_change_test, "AnkiConnect") as connect:
            err = io.StringIO()
            with patch.object(sys, "argv", ["run_live_structural_change_test.py", "--confirm-write", "Personal"]):
                with contextlib.redirect_stderr(err):
                    code = run_live_structural_change_test.main()
        self.assertEqual(code, 2)
        connect.assert_not_called()

if __name__ == "__main__":
    unittest.main()

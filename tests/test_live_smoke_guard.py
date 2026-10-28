from __future__ import annotations
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_live_smoke_test  # noqa: E402


class LiveSmokeGuardTests(unittest.TestCase):
    def test_wrong_confirmation_is_blocked_before_any_network_call(self):
        with patch.object(sys, "argv", ["run_live_smoke_test.py", "--confirm-write", "main"]), \
             patch.object(run_live_smoke_test, "AnkiConnect") as client_class, \
             contextlib.redirect_stderr(io.StringIO()):
            result = run_live_smoke_test.main()
        self.assertEqual(result, 2)
        client_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()

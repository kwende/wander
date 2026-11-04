from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from wander.paths import default_database_path


class DatabasePathTests(unittest.TestCase):
    def test_default_database_is_repository_ledger(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            expected = Path(__file__).resolve().parents[1] / "data" / "wander.db"
            self.assertEqual(expected, default_database_path())

    def test_environment_override_is_shared_configuration(self) -> None:
        configured = Path("D:/wander-data/authoritative.db")
        with patch.dict(os.environ, {"WANDER_DB": str(configured)}, clear=True):
            self.assertEqual(configured, default_database_path())


if __name__ == "__main__":
    unittest.main()

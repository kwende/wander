from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from wander.paths import (
    WanderPaths,
    backup_directory_for_database,
    default_database_path,
)


class DatabasePathTests(unittest.TestCase):
    def test_default_database_is_per_user_application_data(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/test/AppData/Local"}, clear=True):
            expected = Path("C:/Users/test/AppData/Local/Wander/data/wander.db")
            self.assertEqual(expected, default_database_path())

    def test_database_environment_override_is_not_an_ownership_mechanism(self) -> None:
        environment = {
            "LOCALAPPDATA": "C:/Users/test/AppData/Local",
            "WANDER_DB": "D:/wander-data/other.db",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                Path("C:/Users/test/AppData/Local/Wander/data/wander.db"),
                default_database_path(),
            )

    def test_default_layout_separates_data_backups_and_logs(self) -> None:
        paths = WanderPaths.for_current_user("D:/Local")

        self.assertEqual(Path("D:/Local/Wander"), paths.root)
        self.assertEqual(Path("D:/Local/Wander/data/wander.db"), paths.database)
        self.assertEqual(Path("D:/Local/Wander/backups"), paths.backups)
        self.assertEqual(Path("D:/Local/Wander/logs"), paths.logs)

    def test_explicit_database_keeps_its_backups_beside_the_development_data(self) -> None:
        database = Path("D:/repo/data/wander.db")

        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Local"}, clear=True):
            self.assertEqual(
                Path("D:/repo/data/backups"),
                backup_directory_for_database(database),
            )


if __name__ == "__main__":
    unittest.main()

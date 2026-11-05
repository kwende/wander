from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from wander.persistence import PersistenceError, import_legacy_database
from wander.store import SCHEMA_VERSION, WanderStore


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PersistenceBootstrapTests(unittest.TestCase):
    def test_import_preserves_source_and_semantic_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "legacy" / "wander.db"
            destination = root / "installed" / "data" / "wander.db"
            backups = root / "installed" / "backups"
            store = WanderStore(source)
            journey = store.create_journey(
                "import-me",
                "Import me",
                "Prove the application-owned migration preserves the ledger.",
            )
            store.add_work_item("import-me", journey["version"], "Preserved work")
            store.create_jaunt("Preserved jaunt", "Keep these notes")
            source_hash = sha256(source)

            receipt = import_legacy_database(source, destination, backups)

            self.assertEqual(source_hash, sha256(source))
            self.assertTrue(destination.is_file())
            self.assertTrue(Path(receipt.backup).is_file())
            self.assertEqual(SCHEMA_VERSION, receipt.destination_schema)
            self.assertEqual(1, receipt.inventory["journeys"]["count"])
            self.assertEqual(1, receipt.inventory["jaunts"]["count"])
            self.assertEqual("Import me", WanderStore(destination).get_journey("import-me")["title"])
            receipt_document = json.loads(Path(receipt.backup).with_suffix(".json").read_text())
            self.assertEqual(str(source.resolve()), receipt_document["source"])
            self.assertEqual(receipt.backup_sha256, receipt_document["backup_sha256"])
            self.assertEqual([], list(root.rglob("*.tmp")))

    def test_failed_import_leaves_source_and_destination_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "corrupt.db"
            destination = root / "installed" / "wander.db"
            backups = root / "backups"
            source.write_bytes(b"not a sqlite database")
            source_hash = sha256(source)

            with self.assertRaisesRegex(PersistenceError, "SQLite migration failed"):
                import_legacy_database(source, destination, backups)

            self.assertEqual(source_hash, sha256(source))
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.rglob("*.tmp")))

    def test_import_refuses_to_overwrite_an_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.db"
            destination = root / "destination.db"
            WanderStore(source)
            destination.write_bytes(b"keep me")

            with self.assertRaisesRegex(PersistenceError, "Refusing to overwrite"):
                import_legacy_database(source, destination, root / "backups")

            self.assertEqual(b"keep me", destination.read_bytes())

    def test_import_refuses_a_newer_schema_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "future.db"
            destination = root / "installed.db"
            backups = root / "backups"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE future_data (value TEXT NOT NULL)")
                connection.execute("PRAGMA user_version = 5")
                connection.commit()

            with self.assertRaisesRegex(PersistenceError, "newer than"):
                import_legacy_database(source, destination, backups)

            self.assertFalse(destination.exists())
            self.assertFalse(backups.exists())

    def test_schema_migration_failure_keeps_verified_backup_but_publishes_no_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "ambiguous.db"
            destination = root / "installed.db"
            backups = root / "backups"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute(
                    "CREATE TABLE projects (id TEXT, slug TEXT, status TEXT, version INTEGER)"
                )
                connection.execute(
                    "CREATE TABLE journeys (id TEXT, slug TEXT, status TEXT, version INTEGER)"
                )
                connection.commit()
            source_hash = sha256(source)

            with self.assertRaisesRegex(PersistenceError, "schema migration failed"):
                import_legacy_database(source, destination, backups)

            self.assertEqual(source_hash, sha256(source))
            self.assertFalse(destination.exists())
            self.assertEqual(1, len(list(backups.glob("first-desktop-import-*.db"))))
            self.assertEqual([], list(backups.glob("*.json")))
            self.assertEqual([], list(root.rglob("*.tmp")))

    def test_import_migrates_a_pre_versioned_database_after_preserving_its_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "legacy.db"
            destination = root / "installed.db"
            backups = root / "backups"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
                connection.execute("INSERT INTO sentinel VALUES ('legacy')")
                connection.execute("PRAGMA user_version = 0")
                connection.commit()

            receipt = import_legacy_database(source, destination, backups)

            self.assertEqual(0, receipt.source_schema)
            self.assertEqual(SCHEMA_VERSION, receipt.destination_schema)
            with closing(sqlite3.connect(receipt.backup)) as connection:
                self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
                self.assertEqual(
                    "legacy", connection.execute("SELECT value FROM sentinel").fetchone()[0]
                )
            with closing(sqlite3.connect(destination)) as connection:
                self.assertEqual(
                    SCHEMA_VERSION,
                    connection.execute("PRAGMA user_version").fetchone()[0],
                )


if __name__ == "__main__":
    unittest.main()

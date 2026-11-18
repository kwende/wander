from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from wander import persistence as persistence_module
from wander.persistence import (
    PersistenceError,
    create_verified_snapshot,
    identity_path_for_database,
    import_legacy_database,
    restore_ledger,
    verify_snapshot,
)
from wander.store import NotFoundError, SCHEMA_VERSION, WanderStore


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PersistenceBootstrapTests(unittest.TestCase):
    def test_restore_round_trip_preserves_a_safety_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "data" / "wander.db"
            backups = root / "backups"
            store = WanderStore(database)
            original = store.create_journey(
                "restore-me", "Restore me", "Return to the snapshotted version"
            )
            snapshot = create_verified_snapshot(database, backups / "snapshots")
            self.assertIsNotNone(snapshot)
            store.add_work_item("restore-me", original["version"], "Later mutation")

            receipt = restore_ledger(snapshot, database, backups, SCHEMA_VERSION)

            restored = WanderStore(database).get_journey("restore-me")
            self.assertEqual(original["version"], restored["version"])
            self.assertEqual([], restored["work_items"])
            self.assertEqual("snapshot", receipt.source_kind)
            self.assertTrue(Path(receipt.safety_backup).is_file())
            self.assertTrue(Path(receipt.safety_backup).with_suffix(".json").is_file())
            self.assertEqual(
                receipt.restored_ledger_id,
                json.loads(
                    identity_path_for_database(database).read_text(encoding="utf-8")
                )["ledger_id"],
            )
            self.assertFalse((backups / "restores" / "restore-pending.json").exists())

    def test_restore_refuses_a_snapshot_from_another_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / "destination.db"
            other = root / "other.db"
            destination_store = WanderStore(destination)
            destination_store.create_journey("keep", "Keep", "Keep this ledger")
            other_store = WanderStore(other)
            other_store.create_journey("other", "Other", "Different ledger")
            snapshot = create_verified_snapshot(other, root / "snapshots")

            with self.assertRaisesRegex(PersistenceError, "different Wander ledger"):
                restore_ledger(snapshot, destination, root / "backups", SCHEMA_VERSION)

            self.assertEqual("Keep", WanderStore(destination).get_journey("keep")["title"])
            self.assertFalse((root / "backups").exists())

    def test_interrupted_restore_can_finish_from_pending_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "data" / "wander.db"
            backups = root / "backups"
            store = WanderStore(database)
            journey = store.create_journey(
                "interrupted", "Interrupted", "Recover an interrupted restore"
            )
            snapshot = create_verified_snapshot(database, backups / "snapshots")
            self.assertIsNotNone(snapshot)
            store.add_work_item("interrupted", journey["version"], "Later mutation")
            identity_path = identity_path_for_database(database)
            original_write = persistence_module._write_json_atomic

            def fail_identity_write(payload: object, destination: Path) -> None:
                if destination == identity_path:
                    raise OSError("identity publish interrupted")
                original_write(payload, destination)

            with (
                patch(
                    "wander.persistence._write_json_atomic",
                    side_effect=fail_identity_write,
                ),
                self.assertRaisesRegex(OSError, "identity publish interrupted"),
            ):
                restore_ledger(snapshot, database, backups, SCHEMA_VERSION)

            pending = backups / "restores" / "restore-pending.json"
            self.assertTrue(pending.is_file())
            self.assertEqual(
                [], WanderStore(database).get_journey("interrupted")["work_items"]
            )

            receipt = restore_ledger(snapshot, database, backups, SCHEMA_VERSION)

            self.assertFalse(pending.exists())
            self.assertEqual(receipt.restored_ledger_id, store.ledger_identity()["ledger_id"])

    def test_restore_accepts_verified_pre_identity_import_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = root / "legacy.db"
            destination = root / "installed" / "data" / "wander.db"
            backups = root / "installed" / "backups"
            legacy_store = WanderStore(legacy)
            legacy_store.create_journey(
                "legacy", "Legacy", "Restore the November-style import backup"
            )
            with closing(sqlite3.connect(legacy)) as connection:
                connection.execute("DROP TRIGGER mark_ledger_nonempty_on_journey")
                connection.execute("DROP TRIGGER mark_ledger_nonempty_on_jaunt")
                connection.execute("DROP TABLE ledger_metadata")
                connection.execute("PRAGMA user_version = 4")
                connection.commit()
            imported = import_legacy_database(legacy, destination, backups)
            WanderStore(destination).create_journey(
                "later", "Later", "This should disappear after restore"
            )

            restored = restore_ledger(
                imported.backup,
                destination,
                backups,
                SCHEMA_VERSION,
            )

            restored_store = WanderStore(destination)
            self.assertEqual("Legacy", restored_store.get_journey("legacy")["title"])
            with self.assertRaisesRegex(NotFoundError, "not found"):
                restored_store.get_journey("later")
            self.assertEqual("first-import-backup", restored.source_kind)
            self.assertNotEqual(imported.ledger_id, restored.restored_ledger_id)

    def test_verified_snapshot_is_skipped_until_semantic_state_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "wander.db"
            snapshots = root / "snapshots"
            store = WanderStore(database)

            first = create_verified_snapshot(database, snapshots)
            self.assertIsNotNone(first)
            manifest = verify_snapshot(first)
            self.assertEqual(store.ledger_identity()["ledger_id"], manifest.ledger_id)
            self.assertEqual(SCHEMA_VERSION, manifest.schema_version)
            self.assertIsNone(create_verified_snapshot(database, snapshots))

            store.create_journey("changed", "Changed", "Create a changed snapshot")
            second = create_verified_snapshot(database, snapshots)

            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(1, verify_snapshot(second).inventory["journeys"]["count"])

    def test_snapshot_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "wander.db"
            WanderStore(database)
            snapshot = create_verified_snapshot(database, root / "snapshots")
            self.assertIsNotNone(snapshot)
            with snapshot.open("ab") as stream:
                stream.write(b"corrupt")

            with self.assertRaisesRegex(PersistenceError, "size does not match"):
                verify_snapshot(snapshot)

    def test_snapshot_retention_removes_only_old_snapshot_generations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "wander.db"
            snapshots = root / "snapshots"
            store = WanderStore(database)
            unrelated_backup = snapshots / "first-desktop-import.db"
            snapshots.mkdir()
            unrelated_backup.write_bytes(b"preserve")
            created: list[Path] = []
            for index in range(3):
                store.create_jaunt(f"Jaunt {index}")
                snapshot = create_verified_snapshot(database, snapshots, retention=2)
                self.assertIsNotNone(snapshot)
                created.append(snapshot)

            self.assertFalse(created[0].exists())
            self.assertFalse(created[0].with_suffix(".json").exists())
            self.assertTrue(created[1].exists())
            self.assertTrue(created[2].exists())
            self.assertEqual(b"preserve", unrelated_backup.read_bytes())
            self.assertEqual(2, len(list(snapshots.glob("ledger-snapshot-*.json"))))

    def test_snapshot_manifest_failure_publishes_no_partial_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "wander.db"
            snapshots = root / "snapshots"
            WanderStore(database)

            with (
                patch(
                    "wander.persistence._write_json_atomic",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                create_verified_snapshot(database, snapshots)

            self.assertEqual([], list(snapshots.glob("ledger-snapshot-*")))

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
            identity_document = json.loads(
                identity_path_for_database(destination).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt.ledger_id, identity_document["ledger_id"])
            self.assertEqual(str(destination.resolve()), identity_document["database"])
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
                connection.execute("PRAGMA user_version = 6")
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

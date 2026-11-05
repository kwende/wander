from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from wander.store import ConflictError, ValidationError, WanderStore


class JauntStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "wander.db"
        self.store = WanderStore(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_jaunt_is_created_without_a_journey(self) -> None:
        created = self.store.create_jaunt(
            "Investigate Grover C Dils network issues",
            "Review the Escalated Support thread and recent diagnostics.",
        )

        jaunt_id = created["changed"]["jaunt_ids"][0]
        jaunt = self.store.get_jaunt(jaunt_id)

        self.assertEqual("open", jaunt["status"])
        self.assertEqual(1, jaunt["version"])
        self.assertNotIn("journey_id", jaunt)
        self.assertIsNone(jaunt["promoted_journey_id"])
        self.assertEqual("jaunt.created", jaunt["history"][0]["operation"])
        self.assertEqual(
            "Review the Escalated Support thread and recent diagnostics.",
            jaunt["history"][0]["notes_snapshot"],
        )
        self.assertEqual("", jaunt["history"][0]["source_thread"])
        self.assertEqual([], self.store.list_journeys())

    def test_edit_uses_independent_optimistic_version_and_history(self) -> None:
        created = self.store.create_jaunt("Inspect a machine")
        jaunt_id = created["jaunt"]

        updated = self.store.update_jaunt(
            jaunt_id,
            created["version"],
            notes="The machine is reachable at 172.17.5.138.",
        )

        with self.assertRaises(ConflictError):
            self.store.update_jaunt(
                jaunt_id,
                created["version"],
                notes="Overwrite newer notes",
            )

        jaunt = self.store.get_jaunt(jaunt_id)
        self.assertEqual(2, updated["version"])
        self.assertEqual("The machine is reachable at 172.17.5.138.", jaunt["notes"])
        self.assertEqual(
            ["jaunt.updated", "jaunt.created"],
            [event["operation"] for event in jaunt["history"]],
        )
        self.assertEqual(
            ["The machine is reachable at 172.17.5.138.", ""],
            [event["notes_snapshot"] for event in jaunt["history"]],
        )

    def test_completed_jaunt_can_be_reopened_but_not_edited_before_reopening(self) -> None:
        created = self.store.create_jaunt("Handle a bounded request", "Initial notes")
        jaunt_id = created["jaunt"]

        completed = self.store.complete_jaunt(
            jaunt_id,
            created["version"],
            "Resolved directly without broader work.",
        )

        jaunt = self.store.get_jaunt(jaunt_id)
        self.assertEqual("completed", jaunt["status"])
        self.assertIsNotNone(jaunt["completed_at"])
        self.assertEqual(2, completed["version"])
        with self.assertRaisesRegex(ValidationError, "only open jaunts"):
            self.store.update_jaunt(
                jaunt_id,
                completed["version"],
                notes="Late edit",
            )
        with self.assertRaisesRegex(ValidationError, "only open jaunts"):
            self.store.complete_jaunt(jaunt_id, completed["version"])

        reopened = self.store.reopen_jaunt(
            jaunt_id,
            completed["version"],
            "The request needs another look.",
        )
        self.store.update_jaunt(
            jaunt_id,
            reopened["version"],
            notes="Additional investigation is active.",
        )

        jaunt = self.store.get_jaunt(jaunt_id)
        self.assertEqual("open", jaunt["status"])
        self.assertIsNone(jaunt["completed_at"])
        self.assertEqual(
            ["jaunt.updated", "jaunt.reopened", "jaunt.completed", "jaunt.created"],
            [event["operation"] for event in jaunt["history"]],
        )

    def test_list_filters_status_and_rejects_unknown_status(self) -> None:
        open_jaunt = self.store.create_jaunt("Open jaunt")
        completed_jaunt = self.store.create_jaunt("Completed jaunt")
        self.store.complete_jaunt(
            completed_jaunt["jaunt"],
            completed_jaunt["version"],
        )

        self.assertEqual([open_jaunt["jaunt"]], [row["id"] for row in self.store.list_jaunts("open")])
        self.assertEqual(
            [completed_jaunt["jaunt"]],
            [row["id"] for row in self.store.list_jaunts("completed")],
        )
        with self.assertRaises(ValidationError):
            self.store.list_jaunts("active")

    def test_update_requires_a_change_and_nonempty_title(self) -> None:
        created = self.store.create_jaunt("A small task")

        with self.assertRaises(ValidationError):
            self.store.update_jaunt(created["jaunt"], created["version"])
        with self.assertRaises(ValidationError):
            self.store.update_jaunt(created["jaunt"], created["version"], title="  ")
        with self.assertRaises(ValidationError):
            self.store.update_jaunt(
                created["jaunt"],
                created["version"],
                title="A small task",
            )

    def test_legacy_jaunt_schema_is_migrated_without_losing_history(self) -> None:
        legacy_database = Path(self.temporary_directory.name) / "legacy-jaunts.db"
        connection = sqlite3.connect(legacy_database)
        try:
            connection.executescript(
                """
                CREATE TABLE jaunts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE jaunt_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jaunt_id TEXT NOT NULL REFERENCES jaunts(id) ON DELETE CASCADE,
                    operation TEXT NOT NULL,
                    before_version INTEGER NOT NULL,
                    after_version INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    source_thread TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                INSERT INTO jaunts (
                    id, title, notes, status, version, created_at, updated_at
                ) VALUES (
                    'legacy-jaunt', 'Existing jaunt', 'Existing notes', 'open', 1,
                    '2026-11-04T00:00:00+00:00', '2026-11-04T00:00:00+00:00'
                );
                INSERT INTO jaunt_events (
                    jaunt_id, operation, before_version, after_version,
                    summary, source_thread, created_at
                ) VALUES (
                    'legacy-jaunt', 'jaunt.created', 0, 1, 'Existing history', '',
                    '2026-11-04T00:00:00+00:00'
                );
                """
            )
        finally:
            connection.close()

        migrated_store = WanderStore(legacy_database)
        legacy_jaunt = migrated_store.get_jaunt("legacy-jaunt")
        created = migrated_store.create_jaunt("Created after migration")

        self.assertIsNone(legacy_jaunt["promoted_journey_id"])
        self.assertEqual("Existing history", legacy_jaunt["history"][0]["summary"])
        self.assertEqual("", legacy_jaunt["history"][0]["title_snapshot"])
        self.assertEqual("open", migrated_store.get_jaunt(created["jaunt"])["status"])


if __name__ == "__main__":
    unittest.main()

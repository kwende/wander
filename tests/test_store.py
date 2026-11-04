from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from wander.store import ConflictError, OwnershipError, ValidationError, WanderStore


class WanderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "wander.db"
        self.store = WanderStore(self.database_path, "test-thread")
        self.journey = self.store.create_journey(
            "camera-reboot",
            "Camera reboot investigation",
            "Explain and mitigate camera frame starvation.",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def journey_state(self) -> dict:
        return self.store.get_journey("camera-reboot")

    def test_mutations_return_compact_chainable_deltas(self) -> None:
        added = self.store.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Collect evidence",
        )
        item_id = added["changed"]["work_item_ids"][0]
        started = self.store.start_work_item(
            "camera-reboot",
            item_id,
            added["version"],
            "Collecting evidence",
        )

        self.assertEqual(
            {"journey", "version", "operation", "changed", "writer_warning"},
            set(started),
        )
        self.assertEqual("camera-reboot", started["journey"])
        self.assertEqual("work_item.active", started["operation"])
        self.assertEqual({"work_item_ids": [item_id]}, started["changed"])
        self.assertEqual(3, started["version"])
        self.assertNotIn("work_items", started)
        self.assertNotIn("recent_events", started)

    def test_stale_writer_is_rejected(self) -> None:
        version = self.journey["version"]
        self.store.add_work_item("camera-reboot", version, "Collect evidence")

        with self.assertRaises(ConflictError):
            self.store.add_work_item("camera-reboot", version, "Overwrite newer state")

    def test_completion_requires_active_item_and_evidence(self) -> None:
        journey = self.store.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Prove recovery",
            "A controlled camera fault recovers.",
        )
        item_id = journey["changed"]["work_item_ids"][0]

        with self.assertRaises(ValidationError):
            self.store.complete_work_item(
                "camera-reboot",
                item_id,
                journey["version"],
                "",
                "Done",
            )

        journey = self.store.start_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Beginning controlled test",
        )
        journey = self.store.complete_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Tracker logs show frames resumed after reset.",
            "Controlled recovery passed",
        )

        state = self.journey_state()
        self.assertEqual("complete", state["work_items"][0]["status"])
        self.assertEqual(4, journey["version"])

    def test_parent_cannot_complete_before_child_items(self) -> None:
        journey = self.store.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Implement recovery",
        )
        parent_id = journey["changed"]["work_item_ids"][0]
        journey = self.store.add_work_item(
            "camera-reboot",
            journey["version"],
            "Validate recovery",
            parent_id=parent_id,
        )
        child_id = journey["changed"]["work_item_ids"][0]
        journey = self.store.start_work_item(
            "camera-reboot",
            parent_id,
            journey["version"],
            "Implementing recovery",
        )

        with self.assertRaises(ValidationError):
            self.store.complete_work_item(
                "camera-reboot",
                parent_id,
                journey["version"],
                "Parent evidence",
                "Parent complete",
            )

        journey = self.store.start_work_item(
            "camera-reboot",
            child_id,
            journey["version"],
            "Validating recovery",
        )
        journey = self.store.complete_work_item(
            "camera-reboot",
            child_id,
            journey["version"],
            "Child evidence",
            "Child complete",
        )
        journey = self.store.complete_work_item(
            "camera-reboot",
            parent_id,
            journey["version"],
            "Parent evidence",
            "Parent complete",
        )

        parent = next(
            item
            for item in self.journey_state()["work_items"]
            if item["id"] == parent_id
        )
        self.assertEqual("complete", parent["status"])

    def test_completion_atomically_attaches_commit_links(self) -> None:
        journey = self.store.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Implement recovery",
        )
        item_id = journey["changed"]["work_item_ids"][0]
        journey = self.store.start_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Implementing recovery",
        )

        journey = self.store.complete_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Recovery tests pass.",
            "Recovery implemented",
            [
                "https://github.com/Ocuvera/Ocuvera/commit/0123456789abcdef0123456789abcdef01234567",
                "https://github.com/Ocuvera/Ocuvera/commit/89abcdef0123456789abcdef0123456789abcdef",
            ],
        )

        item = self.journey_state()["work_items"][0]
        self.assertEqual("complete", item["status"])
        self.assertEqual(["0123456789ab", "89abcdef0123"], [link["label"] for link in item["links"]])
        self.assertEqual(
            [link["id"] for link in item["links"]],
            journey["changed"]["work_item_link_ids"],
        )
        self.assertEqual(4, journey["version"])

    def test_invalid_commit_link_leaves_item_active(self) -> None:
        journey = self.store.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Implement recovery",
        )
        item_id = journey["changed"]["work_item_ids"][0]
        journey = self.store.start_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Implementing recovery",
        )

        with self.assertRaises(ValidationError):
            self.store.complete_work_item(
                "camera-reboot",
                item_id,
                journey["version"],
                "Recovery tests pass.",
                "Recovery implemented",
                ["https://example.test/not-a-github-commit"],
            )

        unchanged = self.store.get_journey("camera-reboot")
        self.assertEqual("active", unchanged["work_items"][0]["status"])
        self.assertEqual([], unchanged["work_items"][0]["links"])
        self.assertEqual(journey["version"], unchanged["version"])

    def test_commit_link_can_be_attached_after_completion(self) -> None:
        journey = self.store.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Document recovery",
        )
        item_id = journey["changed"]["work_item_ids"][0]

        with self.assertRaises(ValidationError):
            self.store.add_work_item_link(
                "camera-reboot",
                item_id,
                journey["version"],
                "https://github.com/Ocuvera/Ocuvera/commit/0123456789abcdef0123456789abcdef01234567",
            )

        journey = self.store.start_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Writing documentation",
        )
        journey = self.store.complete_work_item(
            "camera-reboot",
            item_id,
            journey["version"],
            "Documentation reviewed.",
            "Documentation complete",
        )
        journey = self.store.add_work_item_link(
            "camera-reboot",
            item_id,
            journey["version"],
            "https://github.com/Ocuvera/Ocuvera/commit/0123456789abcdef0123456789abcdef01234567",
        )

        state = self.journey_state()
        self.assertEqual("0123456789ab", state["work_items"][0]["links"][0]["label"])
        self.assertEqual("work_item_link.added", state["recent_events"][0]["operation"])

    def test_checkpoint_is_returned_as_resume_context(self) -> None:
        journey = self.store.record_checkpoint(
            "camera-reboot",
            self.journey["version"],
            "Evidence collection is ready.",
            "Controlled reproduction",
            "The camera accessor owns recovery.",
            "Keep the reset local to the accessor.",
            "Only one reset process may own the camera.",
            "Can USB corruption be reproduced?",
            "Run the depth-only perturbation.",
        )
        context = self.store.get_resume_context("camera-reboot")

        self.assertEqual(journey["version"], context["journey"]["version"])
        self.assertEqual(
            [context["latest_checkpoint"]["id"]],
            journey["changed"]["checkpoint_ids"],
        )
        self.assertEqual(
            "Run the depth-only perturbation.",
            context["latest_checkpoint"]["resume_here"],
        )

    def test_thread_link_must_use_supported_codex_scheme(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.attach_thread(
                "camera-reboot",
                self.journey["version"],
                "https://example.test/thread",
                "Wrong link",
                "primary",
            )

    def test_thread_mutation_returns_stable_reference_id(self) -> None:
        result = self.store.attach_thread(
            "camera-reboot",
            self.journey["version"],
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67",
            "Investigation",
            "primary",
        )
        state = self.journey_state()

        self.assertEqual(
            [state["threads"][0]["id"]],
            result["changed"]["thread_reference_ids"],
        )

    def test_unidentified_server_can_bind_once_and_create_journey(self) -> None:
        unidentified = WanderStore(
            Path(self.temporary_directory.name) / "unidentified.db"
        )

        identity = unidentified.identify_writer_thread(
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67"
        )
        result = unidentified.create_journey(
            "vc-runtime-cleanup",
            "VC Runtime Cleanup",
            "Align the native toolchain and packaged runtime.",
        )

        state = unidentified.get_journey(result["journey"])
        self.assertEqual("identified", identity["status"])
        self.assertEqual(
            "01a0abd2-f583-78a0-b1f5-c14899ee9c67",
            state["owner_thread_id"],
        )
        self.assertEqual(
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67",
            state["recent_events"][0]["source_thread"],
        )

    def test_mcp_session_cannot_switch_thread_identity(self) -> None:
        unidentified = WanderStore(
            Path(self.temporary_directory.name) / "identity-switch.db"
        )
        unidentified.identify_writer_thread(
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67"
        )

        with self.assertRaises(OwnershipError):
            unidentified.identify_writer_thread(
                "codex://threads/01a18d56-4e60-7050-a88d-c64cc83d5bae"
            )

    def test_journey_links_are_versioned_and_ordered(self) -> None:
        journey = self.store.add_journey_link(
            "camera-reboot",
            self.journey["version"],
            "Pull request #1127",
            "https://github.com/Ocuvera/Ocuvera/pull/1127",
            sequence=20,
        )
        journey = self.store.add_journey_link(
            "camera-reboot",
            journey["version"],
            "Issue #1099",
            "https://github.com/Ocuvera/Ocuvera/issues/1099",
            sequence=10,
        )

        state = self.journey_state()
        self.assertEqual(
            ["Issue #1099", "Pull request #1127"],
            [link["label"] for link in state["links"]],
        )
        self.assertEqual("journey_link.added", state["recent_events"][0]["operation"])
        self.assertEqual(3, journey["version"])

    def test_journey_link_rejects_unsafe_url(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.add_journey_link(
                "camera-reboot",
                self.journey["version"],
                "Unsafe",
                "javascript:alert(1)",
            )

    def test_journey_link_can_be_removed_with_history(self) -> None:
        journey = self.store.add_journey_link(
            "camera-reboot",
            self.journey["version"],
            "Issue #1099",
            "https://github.com/Ocuvera/Ocuvera/issues/1099",
        )
        link_id = journey["changed"]["journey_link_ids"][0]

        journey = self.store.remove_journey_link(
            "camera-reboot",
            link_id,
            journey["version"],
            "Issue was superseded",
        )

        state = self.journey_state()
        self.assertEqual([], state["links"])
        self.assertEqual("journey_link.removed", state["recent_events"][0]["operation"])
        self.assertIn("Issue was superseded", state["recent_events"][0]["summary"])

    def test_completed_journey_cannot_be_reactivated(self) -> None:
        journey = self.store.set_journey_status(
            "camera-reboot",
            self.journey["version"],
            "completed",
            "Investigation complete",
            "The release passed its production observation window.",
        )

        with self.assertRaises(ValidationError):
            self.store.set_journey_status(
                "camera-reboot",
                journey["version"],
                "active",
                "Reopen it",
            )

    def test_non_owner_can_read_and_write_with_warning(self) -> None:
        other_thread = WanderStore(self.database_path, "other-thread")

        journey = other_thread.add_work_item(
            "camera-reboot",
            self.journey["version"],
            "Potentially conflicting work",
        )

        self.assertIn("codex://threads/test-thread", journey["writer_warning"])
        self.assertIn("write succeeded", journey["writer_warning"])
        self.assertEqual("test-thread", self.journey_state()["owner_thread_id"])

    def test_owner_can_release_and_another_thread_can_claim(self) -> None:
        journey = self.store.release_journey(
            "camera-reboot",
            self.journey["version"],
            "Handing work to another conversation",
        )
        other_thread = WanderStore(self.database_path, "other-thread")
        journey = other_thread.claim_journey("camera-reboot", journey["version"])
        journey = other_thread.add_work_item(
            "camera-reboot",
            journey["version"],
            "Continue from new thread",
        )

        state = self.journey_state()
        self.assertEqual("other-thread", state["owner_thread_id"])
        self.assertEqual(1, len(state["work_items"]))

    def test_legacy_project_schema_is_migrated_without_losing_history(self) -> None:
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE projects (
                    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    objective TEXT NOT NULL, status TEXT NOT NULL, current_summary TEXT NOT NULL,
                    current_phase TEXT NOT NULL, owner_thread_id TEXT, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE work_items (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    parent_id TEXT REFERENCES work_items(id), sequence INTEGER NOT NULL,
                    title TEXT NOT NULL, status TEXT NOT NULL, completion_criteria TEXT NOT NULL,
                    completion_evidence TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE project_links (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    label TEXT NOT NULL, url TEXT NOT NULL, sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id, url)
                );
                CREATE TABLE work_item_links (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    work_item_id TEXT NOT NULL REFERENCES work_items(id), label TEXT NOT NULL,
                    url TEXT NOT NULL, sequence INTEGER NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(work_item_id, url)
                );
                CREATE TABLE thread_references (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    deep_link TEXT NOT NULL, title TEXT NOT NULL, role TEXT NOT NULL,
                    last_activity TEXT NOT NULL, UNIQUE(project_id, deep_link)
                );
                CREATE TABLE checkpoints (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    current_model TEXT NOT NULL, last_decision TEXT NOT NULL,
                    invariants TEXT NOT NULL, open_questions TEXT NOT NULL,
                    resume_here TEXT NOT NULL, source_thread TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id), operation TEXT NOT NULL,
                    before_version INTEGER NOT NULL, after_version INTEGER NOT NULL,
                    summary TEXT NOT NULL, evidence TEXT NOT NULL,
                    source_thread TEXT NOT NULL, created_at TEXT NOT NULL
                );

                INSERT INTO projects VALUES (
                    'journey-1', 'legacy-work', 'Legacy work', 'Preserve this ledger.',
                    'active', 'Still active', 'Validation', 'legacy-thread', 4,
                    '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00'
                );
                INSERT INTO work_items VALUES (
                    'item-1', 'journey-1', NULL, 10, 'Preserve the item', 'active',
                    'It survives.', '', 'Historical notes',
                    '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00'
                );
                INSERT INTO project_links VALUES (
                    'link-1', 'journey-1', 'Issue', 'https://github.com/kwende/wander/issues/1',
                    10, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                );
                INSERT INTO thread_references VALUES (
                    'thread-1', 'journey-1', 'codex://threads/legacy-thread',
                    'Legacy conversation', 'primary', '2026-01-02T00:00:00+00:00'
                );
                INSERT INTO checkpoints VALUES (
                    'checkpoint-1', 'journey-1', 'Current model', 'Last decision',
                    'Invariant', 'Question', 'Resume here', 'codex://threads/legacy-thread',
                    '2026-01-02T00:00:00+00:00'
                );
                INSERT INTO events (
                    project_id, operation, before_version, after_version,
                    summary, evidence, source_thread, created_at
                ) VALUES (
                    'journey-1', 'project.created', 0, 1, 'Created project: Legacy work',
                    '', 'codex://threads/legacy-thread', '2026-01-01T00:00:00+00:00'
                );
                """
            )

        migrated = WanderStore(legacy_path, "legacy-thread")
        state = migrated.get_journey("legacy-work")

        self.assertEqual("Legacy work", state["title"])
        self.assertEqual("Preserve the item", state["work_items"][0]["title"])
        self.assertEqual("Issue", state["links"][0]["label"])
        self.assertEqual("Legacy conversation", state["threads"][0]["title"])
        self.assertEqual("Resume here", state["latest_checkpoint"]["resume_here"])
        self.assertEqual("project.created", state["recent_events"][0]["operation"])

        with closing(sqlite3.connect(legacy_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            work_item_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(work_items)")
            }
            self.assertIn("journeys", tables)
            self.assertIn("journey_links", tables)
            self.assertNotIn("projects", tables)
            self.assertNotIn("project_links", tables)
            self.assertIn("journey_id", work_item_columns)
            self.assertNotIn("project_id", work_item_columns)
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual(3, connection.execute("PRAGMA user_version").fetchone()[0])


if __name__ == "__main__":
    unittest.main()


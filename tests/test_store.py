from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wander.store import ConflictError, OwnershipError, ValidationError, WanderStore


class WanderStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "wander.db"
        self.store = WanderStore(self.database_path, "test-thread")
        self.project = self.store.create_project(
            "camera-reboot",
            "Camera reboot investigation",
            "Explain and mitigate camera frame starvation.",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_stale_writer_is_rejected(self) -> None:
        version = self.project["version"]
        self.store.add_work_item("camera-reboot", version, "Collect evidence")

        with self.assertRaises(ConflictError):
            self.store.add_work_item("camera-reboot", version, "Overwrite newer state")

    def test_completion_requires_active_item_and_evidence(self) -> None:
        project = self.store.add_work_item(
            "camera-reboot",
            self.project["version"],
            "Prove recovery",
            "A controlled camera fault recovers.",
        )
        item_id = project["work_items"][0]["id"]

        with self.assertRaises(ValidationError):
            self.store.complete_work_item(
                "camera-reboot",
                item_id,
                project["version"],
                "",
                "Done",
            )

        project = self.store.start_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Beginning controlled test",
        )
        project = self.store.complete_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Tracker logs show frames resumed after reset.",
            "Controlled recovery passed",
        )

        self.assertEqual("complete", project["work_items"][0]["status"])
        self.assertEqual(4, project["version"])

    def test_completion_atomically_attaches_commit_links(self) -> None:
        project = self.store.add_work_item(
            "camera-reboot",
            self.project["version"],
            "Implement recovery",
        )
        item_id = project["work_items"][0]["id"]
        project = self.store.start_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Implementing recovery",
        )

        project = self.store.complete_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Recovery tests pass.",
            "Recovery implemented",
            [
                "https://github.com/Ocuvera/Ocuvera/commit/0123456789abcdef0123456789abcdef01234567",
                "https://github.com/Ocuvera/Ocuvera/commit/89abcdef0123456789abcdef0123456789abcdef",
            ],
        )

        item = project["work_items"][0]
        self.assertEqual("complete", item["status"])
        self.assertEqual(["0123456789ab", "89abcdef0123"], [link["label"] for link in item["links"]])
        self.assertEqual(4, project["version"])

    def test_invalid_commit_link_leaves_item_active(self) -> None:
        project = self.store.add_work_item(
            "camera-reboot",
            self.project["version"],
            "Implement recovery",
        )
        item_id = project["work_items"][0]["id"]
        project = self.store.start_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Implementing recovery",
        )

        with self.assertRaises(ValidationError):
            self.store.complete_work_item(
                "camera-reboot",
                item_id,
                project["version"],
                "Recovery tests pass.",
                "Recovery implemented",
                ["https://example.test/not-a-github-commit"],
            )

        unchanged = self.store.get_project("camera-reboot")
        self.assertEqual("active", unchanged["work_items"][0]["status"])
        self.assertEqual([], unchanged["work_items"][0]["links"])
        self.assertEqual(project["version"], unchanged["version"])

    def test_commit_link_can_be_attached_after_completion(self) -> None:
        project = self.store.add_work_item(
            "camera-reboot",
            self.project["version"],
            "Document recovery",
        )
        item_id = project["work_items"][0]["id"]

        with self.assertRaises(ValidationError):
            self.store.add_work_item_link(
                "camera-reboot",
                item_id,
                project["version"],
                "https://github.com/Ocuvera/Ocuvera/commit/0123456789abcdef0123456789abcdef01234567",
            )

        project = self.store.start_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Writing documentation",
        )
        project = self.store.complete_work_item(
            "camera-reboot",
            item_id,
            project["version"],
            "Documentation reviewed.",
            "Documentation complete",
        )
        project = self.store.add_work_item_link(
            "camera-reboot",
            item_id,
            project["version"],
            "https://github.com/Ocuvera/Ocuvera/commit/0123456789abcdef0123456789abcdef01234567",
        )

        self.assertEqual("0123456789ab", project["work_items"][0]["links"][0]["label"])
        self.assertEqual("work_item_link.added", project["recent_events"][0]["operation"])

    def test_checkpoint_is_returned_as_resume_context(self) -> None:
        project = self.store.record_checkpoint(
            "camera-reboot",
            self.project["version"],
            "Evidence collection is ready.",
            "Controlled reproduction",
            "The camera accessor owns recovery.",
            "Keep the reset local to the accessor.",
            "Only one reset process may own the camera.",
            "Can USB corruption be reproduced?",
            "Run the depth-only perturbation.",
        )
        context = self.store.get_resume_context("camera-reboot")

        self.assertEqual(project["version"], context["project"]["version"])
        self.assertEqual(
            "Run the depth-only perturbation.",
            context["latest_checkpoint"]["resume_here"],
        )

    def test_thread_link_must_use_supported_codex_scheme(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.attach_thread(
                "camera-reboot",
                self.project["version"],
                "https://example.test/thread",
                "Wrong link",
                "primary",
            )

    def test_unidentified_server_can_bind_once_and_create_project(self) -> None:
        unidentified = WanderStore(
            Path(self.temporary_directory.name) / "unidentified.db"
        )

        identity = unidentified.identify_writer_thread(
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67"
        )
        project = unidentified.create_project(
            "vc-runtime-cleanup",
            "VC Runtime Cleanup",
            "Align the native toolchain and packaged runtime.",
        )

        self.assertEqual("identified", identity["status"])
        self.assertEqual(
            "01a0abd2-f583-78a0-b1f5-c14899ee9c67",
            project["owner_thread_id"],
        )
        self.assertEqual(
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67",
            project["recent_events"][0]["source_thread"],
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

    def test_project_links_are_versioned_and_ordered(self) -> None:
        project = self.store.add_project_link(
            "camera-reboot",
            self.project["version"],
            "Pull request #1127",
            "https://github.com/Ocuvera/Ocuvera/pull/1127",
            sequence=20,
        )
        project = self.store.add_project_link(
            "camera-reboot",
            project["version"],
            "Issue #1099",
            "https://github.com/Ocuvera/Ocuvera/issues/1099",
            sequence=10,
        )

        self.assertEqual(
            ["Issue #1099", "Pull request #1127"],
            [link["label"] for link in project["links"]],
        )
        self.assertEqual("project_link.added", project["recent_events"][0]["operation"])
        self.assertEqual(3, project["version"])

    def test_project_link_rejects_unsafe_url(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.add_project_link(
                "camera-reboot",
                self.project["version"],
                "Unsafe",
                "javascript:alert(1)",
            )

    def test_project_link_can_be_removed_with_history(self) -> None:
        project = self.store.add_project_link(
            "camera-reboot",
            self.project["version"],
            "Issue #1099",
            "https://github.com/Ocuvera/Ocuvera/issues/1099",
        )
        link_id = project["links"][0]["id"]

        project = self.store.remove_project_link(
            "camera-reboot",
            link_id,
            project["version"],
            "Issue was superseded",
        )

        self.assertEqual([], project["links"])
        self.assertEqual("project_link.removed", project["recent_events"][0]["operation"])
        self.assertIn("Issue was superseded", project["recent_events"][0]["summary"])

    def test_completed_project_cannot_be_reactivated(self) -> None:
        project = self.store.set_project_status(
            "camera-reboot",
            self.project["version"],
            "completed",
            "Investigation complete",
            "The release passed its production observation window.",
        )

        with self.assertRaises(ValidationError):
            self.store.set_project_status(
                "camera-reboot",
                project["version"],
                "active",
                "Reopen it",
            )

    def test_non_owner_can_read_and_write_with_warning(self) -> None:
        other_thread = WanderStore(self.database_path, "other-thread")

        project = other_thread.add_work_item(
            "camera-reboot",
            self.project["version"],
            "Potentially conflicting work",
        )

        self.assertIn("codex://threads/test-thread", project["writer_warning"])
        self.assertIn("write succeeded", project["writer_warning"])
        self.assertEqual("test-thread", project["owner_thread_id"])

    def test_owner_can_release_and_another_thread_can_claim(self) -> None:
        project = self.store.release_project(
            "camera-reboot",
            self.project["version"],
            "Handing work to another conversation",
        )
        other_thread = WanderStore(self.database_path, "other-thread")
        project = other_thread.claim_project("camera-reboot", project["version"])
        project = other_thread.add_work_item(
            "camera-reboot",
            project["version"],
            "Continue from new thread",
        )

        self.assertEqual("other-thread", project["owner_thread_id"])
        self.assertEqual(1, len(project["work_items"]))


if __name__ == "__main__":
    unittest.main()


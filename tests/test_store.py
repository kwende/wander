from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wander.store import ConflictError, ValidationError, WanderStore


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


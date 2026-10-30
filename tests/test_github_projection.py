from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wander.store import ConflictError, ValidationError, WanderStore


class GitHubProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = WanderStore(
            Path(self.temporary_directory.name) / "wander.db",
            "test-thread",
        )
        self.project = self.store.create_project(
            "wander",
            "Wander Mk 1",
            "Keep durable project state.",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def add_issue_link(self) -> dict:
        self.project = self.store.add_project_link(
            "wander",
            self.project["version"],
            "GitHub issue #1",
            "https://github.com/kwende/wander/issues/1",
        )
        return self.project

    def test_projection_requires_exactly_one_github_issue(self) -> None:
        with self.assertRaisesRegex(ValidationError, "exactly one GitHub issue"):
            self.store.prepare_github_issue_projection("wander")

        self.add_issue_link()
        self.project = self.store.add_project_link(
            "wander",
            self.project["version"],
            "GitHub issue #2",
            "https://github.com/kwende/wander/issues/2",
        )

        with self.assertRaisesRegex(ValidationError, "exactly one GitHub issue"):
            self.store.prepare_github_issue_projection("wander")

    def test_projection_renders_authoritative_status_hierarchy_and_commits(self) -> None:
        self.add_issue_link()
        self.project = self.store.add_work_item(
            "wander",
            self.project["version"],
            "Implement projection",
        )
        parent_id = self.project["work_items"][0]["id"]
        self.project = self.store.add_work_item(
            "wander",
            self.project["version"],
            "Verify GitHub comment",
            parent_id=parent_id,
        )
        self.project = self.store.start_work_item(
            "wander",
            parent_id,
            self.project["version"],
            "Implementing projection",
        )
        self.project = self.store.add_work_item(
            "wander",
            self.project["version"],
            "Document ownership",
        )
        document_id = next(
            item["id"]
            for item in self.project["work_items"]
            if item["title"] == "Document ownership"
        )
        self.project = self.store.start_work_item(
            "wander",
            document_id,
            self.project["version"],
            "Documenting ownership",
        )
        self.project = self.store.complete_work_item(
            "wander",
            document_id,
            self.project["version"],
            "The source-of-truth rule is documented.",
            "Ownership documented",
            [
                "https://github.com/kwende/wander/commit/"
                "0123456789abcdef0123456789abcdef01234567"
            ],
        )
        self.project = self.store.add_work_item(
            "wander",
            self.project["version"],
            "Import GitHub checkboxes",
        )
        abandoned_id = next(
            item["id"]
            for item in self.project["work_items"]
            if item["title"] == "Import GitHub checkboxes"
        )
        self.project = self.store.abandon_work_item(
            "wander",
            abandoned_id,
            self.project["version"],
            "Wander is always authoritative",
        )

        projection = self.store.prepare_github_issue_projection(
            "wander",
            "http://10.20.30.40:8765/?project=wander&shared=1",
        )
        body = projection["comment_body"]

        self.assertEqual("create", projection["operation"])
        self.assertEqual(1, projection["issue"]["number"])
        self.assertIn("GitHub checkbox edits are display-only and never update Wander", body)
        self.assertIn("- [ ] **Implement projection** — **active**", body)
        self.assertIn("  - [ ] Verify GitHub comment", body)
        self.assertIn("- [x] Document ownership ([0123456789ab]", body)
        self.assertIn(
            "- ~~Import GitHub checkboxes~~ — *deferred / not planned*",
            body,
        )
        self.assertIn("Open the live Wander workboard", body)
        self.assertNotIn("The source-of-truth rule is documented.", body)
        self.assertNotIn("Wander is always authoritative", body)

    def test_projection_comment_registration_is_versioned_and_issue_bound(self) -> None:
        self.add_issue_link()

        with self.assertRaisesRegex(ValidationError, "configured GitHub issue"):
            self.store.register_github_projection_comment(
                "wander",
                self.project["version"],
                "https://github.com/kwende/wander/issues/2#issuecomment-123",
            )

        self.project = self.store.register_github_projection_comment(
            "wander",
            self.project["version"],
            "https://github.com/kwende/wander/issues/1#issuecomment-123",
        )
        projection = self.store.prepare_github_issue_projection("wander")

        self.assertEqual("update", projection["operation"])
        self.assertEqual(123, projection["comment"]["comment_id"])
        self.assertEqual("Wander GitHub projection", self.project["links"][1]["label"])
        self.assertEqual("project_link.added", self.project["recent_events"][0]["operation"])

        with self.assertRaisesRegex(ConflictError, "already has"):
            self.store.register_github_projection_comment(
                "wander",
                self.project["version"],
                "https://github.com/kwende/wander/issues/1#issuecomment-456",
            )

    def test_invalid_workboard_url_is_rejected(self) -> None:
        self.add_issue_link()

        with self.assertRaisesRegex(ValidationError, "workboard_url"):
            self.store.prepare_github_issue_projection("wander", "javascript:alert(1)")


if __name__ == "__main__":
    unittest.main()

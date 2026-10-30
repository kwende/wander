from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from wander.store import WanderStore
from wander.web import create_server


class ReadOnlyDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = WanderStore(
            Path(self.temporary_directory.name) / "wander.db",
            "test-thread",
        )
        self.store.create_project("wander", "Wander", "Keep durable project state.")
        self.server = create_server("127.0.0.1", 0, self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_project_api_is_readable(self) -> None:
        project = self.store.get_project("wander")
        self.store.add_project_link(
            "wander",
            project["version"],
            "Issue",
            "https://github.com/kwende/wander/issues/1",
        )
        with urllib.request.urlopen(f"{self.base_url}/api/projects/wander") as response:
            result = json.load(response)
        self.assertEqual("Wander", result["title"])
        self.assertEqual("Issue", result["links"][0]["label"])

    def test_initial_page_includes_wander_tagline(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as response:
            page = response.read().decode("utf-8")
        self.assertIn("Wander", page)
        self.assertIn("Not all who wander are lost.", page)
        self.assertIn("Loading the workboard", page)

    def test_http_mutation_is_rejected(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/projects",
            method="POST",
            data=b"{}",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(405, raised.exception.code)


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wander import mcp_server
from wander.store import WanderStore


class JauntMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "wander.db"
        self.store = WanderStore(database_path, "jaunt-mcp-test")
        self.store_patch = patch.object(mcp_server, "store", new=self.store)
        self.store_patch.start()

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.temporary_directory.cleanup()

    def test_mcp_handlers_cover_independent_jaunt_lifecycle(self) -> None:
        created = mcp_server.create_jaunt("Investigate a machine", "Initial notes")
        jaunt_id = created["jaunt"]

        self.assertEqual([jaunt_id], [row["id"] for row in mcp_server.list_jaunts("open")])
        self.assertIsNone(mcp_server.get_jaunt(jaunt_id)["promoted_journey_id"])

        updated = mcp_server.update_jaunt(
            jaunt_id,
            created["version"],
            notes="Evidence collected.",
        )
        completed = mcp_server.complete_jaunt(
            jaunt_id,
            updated["version"],
            "The bounded request is resolved.",
        )
        reopened = mcp_server.reopen_jaunt(
            jaunt_id,
            completed["version"],
            "More investigation is needed.",
        )

        jaunt = mcp_server.get_jaunt(jaunt_id)
        self.assertEqual("open", jaunt["status"])
        self.assertEqual(reopened["version"], jaunt["version"])
        self.assertEqual(
            ["jaunt.reopened", "jaunt.completed", "jaunt.updated", "jaunt.created"],
            [event["operation"] for event in jaunt["history"]],
        )
        self.assertEqual([], self.store.list_journeys())

    def test_all_jaunt_handlers_are_registered_as_mcp_tools(self) -> None:
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {tool.name for tool in tools}

        self.assertTrue(
            {
                "list_jaunts",
                "get_jaunt",
                "create_jaunt",
                "update_jaunt",
                "complete_jaunt",
                "reopen_jaunt",
            }.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()

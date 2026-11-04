from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from wander import mcp_server
from wander.mathoms import HippoClient, HippoError
from wander.store import ValidationError, WanderStore


class HippoClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = HippoClient("http://localhost:8787/mcp", "test-token")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.application_store = WanderStore(
            Path(self.temporary_directory.name) / "wander.db"
        )
        mcp_server.configure_mcp_server(self.application_store, self.client)

    def tearDown(self) -> None:
        mcp_server.release_mcp_server(self.application_store)
        self.temporary_directory.cleanup()

    def test_record_mathom_uses_session_without_caller_controlled_source(self) -> None:
        with patch.object(
            self.client,
            "_call_tool",
            return_value={"outcomes": []},
        ) as call:
            self.client.record_mathom(
                "codex://threads/thread-1",
                "observation",
                "python.sqlite.connection_context",
                "A sqlite3 connection context manager does not close the connection.",
                1.0,
            )

        tool, arguments = call.call_args.args
        request = arguments["request"]
        self.assertEqual("record_operational_findings", tool)
        self.assertNotIn("sourceSystem", request)
        self.assertEqual("codex://threads/thread-1", request["sourceSessionId"])
        self.assertEqual([], request["findings"][0]["entities"])

    def test_search_mathoms_is_always_scoped_to_wander(self) -> None:
        with patch.object(
            self.client,
            "_call_tool",
            return_value={"findings": []},
        ) as call:
            self.client.search_mathoms("SQLite connection")

        tool, arguments = call.call_args.args
        request = arguments["request"]
        self.assertEqual("recall_operational_context", tool)
        self.assertEqual(["wander"], request["sourceSystems"])
        self.assertEqual(["SQLite connection"], request["concepts"])

    def test_missing_token_fails_only_when_mathom_tool_is_used(self) -> None:
        client = HippoClient("http://localhost:8787/mcp", "")

        with self.assertRaisesRegex(HippoError, "WANDER_HIPPO_TOKEN"):
            client.search_mathoms()

    def test_mathom_mcp_handlers_are_async(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(mcp_server.save_mathom))
        self.assertTrue(inspect.iscoroutinefunction(mcp_server.search_mathoms))

    def test_search_mathom_handler_awaits_async_client(self) -> None:
        with patch.object(
            mcp_server.hippo,
            "search_mathoms_async",
            new=AsyncMock(return_value={"findings": []}),
        ) as search:
            result = asyncio.run(
                mcp_server.search_mathoms("SQLite connection", maximum_results=3)
            )

        self.assertEqual({"findings": []}, result)
        search.assert_awaited_once_with("SQLite connection", None, 3)

    def test_save_mathom_handler_awaits_async_client(self) -> None:
        source_thread_link = (
            "codex://threads/01a0abd2-f583-78a0-b1f5-c14899ee9c67"
        )
        with patch.object(
            mcp_server.hippo,
            "record_mathom_async",
            new=AsyncMock(return_value={"outcomes": []}),
        ) as record:
            result = asyncio.run(
                mcp_server.save_mathom(
                    source_thread_link,
                    "observation",
                    "python.sqlite.connection_context",
                    "A sqlite3 connection context manager does not close the connection.",
                    1.0,
                )
            )

        self.assertEqual({"outcomes": []}, result)
        record.assert_awaited_once_with(
            source_thread_link,
            "observation",
            "python.sqlite.connection_context",
            "A sqlite3 connection context manager does not close the connection.",
            1.0,
            None,
            None,
            None,
            None,
            None,
            None,
        )

    def test_save_mathom_requires_an_explicit_supported_thread_link(self) -> None:
        with self.assertRaisesRegex(ValidationError, "source_thread_link"):
            asyncio.run(
                mcp_server.save_mathom(
                    "https://example.test/not-a-codex-link",
                    "observation",
                    "python.sqlite.connection_context",
                    "Statement",
                    1.0,
                )
            )


if __name__ == "__main__":
    unittest.main()

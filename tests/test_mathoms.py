from __future__ import annotations

import unittest
from unittest.mock import patch

from wander.mathoms import HippoClient, HippoError


class HippoClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = HippoClient("http://localhost:8787/mcp", "test-token")

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


if __name__ == "__main__":
    unittest.main()

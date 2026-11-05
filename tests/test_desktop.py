from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from wander import mcp_server
from wander.desktop import DesktopHost, DesktopStartupError, run_desktop


async def list_mcp_tools(url: str) -> set[str]:
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}


async def create_journey_through_mcp(url: str, slug: str) -> None:
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "create_journey",
                {
                    "slug": slug,
                    "title": "Shared ledger",
                    "objective": "Prove UI and MCP use one application store.",
                },
            )
            if result.is_error:
                raise AssertionError(result.content)


class DesktopHostTests(unittest.TestCase):
    def test_server_becomes_healthy_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            host = DesktopHost.create(
                "127.0.0.1",
                0,
                Path(temporary_directory) / "wander.db",
                mcp_port=0,
            )
            self.assertIs(host.store, mcp_server.store)
            self.assertIs(host.store, host.server.RequestHandlerClass.store)
            host.start()
            thread = host.thread
            mcp_thread = host.mcp_thread
            with urlopen(f"{host.local_url}/api/health", timeout=1) as response:
                health = json.load(response)
            self.assertEqual("ok", health["status"])
            self.assertTrue(thread and thread.is_alive())
            self.assertTrue(mcp_thread and mcp_thread.is_alive())
            self.assertIn("list_journeys", asyncio.run(list_mcp_tools(host.mcp_url)))
            mcp_port = host.mcp_port

            host.stop()

            self.assertFalse(thread and thread.is_alive())
            self.assertFalse(mcp_thread and mcp_thread.is_alive())
            self.assertIsNone(mcp_server.store)
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", host.server.server_port), timeout=0.2)
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", mcp_port), timeout=0.2)

    def test_ui_and_mcp_share_the_application_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            host = DesktopHost.create(
                "127.0.0.1",
                0,
                Path(temporary_directory) / "wander.db",
                mcp_port=0,
            )
            try:
                host.start()
                asyncio.run(create_journey_through_mcp(host.mcp_url, "shared-ledger"))

                with urlopen(
                    f"{host.local_url}/api/journeys/shared-ledger", timeout=1
                ) as response:
                    journey = json.load(response)

                self.assertEqual("Shared ledger", journey["title"])
                self.assertIs(host.store, host.server.RequestHandlerClass.store)
                self.assertEqual("127.0.0.1", host.mcp_transport.config.host)
            finally:
                host.stop()

    def test_port_conflict_is_reported_as_startup_error(self) -> None:
        with socket.socket() as occupied, tempfile.TemporaryDirectory() as temporary_directory:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with self.assertRaisesRegex(DesktopStartupError, "already own that address"):
                DesktopHost.create(
                    "127.0.0.1",
                    port,
                    Path(temporary_directory) / "wander.db",
                    mcp_port=0,
                )

    def test_mcp_port_conflict_is_reported_as_startup_error(self) -> None:
        with socket.socket() as occupied, tempfile.TemporaryDirectory() as temporary_directory:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            host = DesktopHost.create(
                "127.0.0.1",
                0,
                Path(temporary_directory) / "wander.db",
                mcp_port=port,
            )

            with self.assertRaisesRegex(DesktopStartupError, "MCP server could not listen"):
                host.start(timeout_seconds=1)

    def test_mcp_module_cannot_open_a_database_by_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "must-not-exist.db"
            environment = os.environ.copy()
            environment["WANDER_DB"] = str(database_path)

            result = subprocess.run(
                [sys.executable, "-m", "wander.mcp_server"],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("hosted by the Wander desktop application", result.stderr)
            self.assertFalse(database_path.exists())

    def test_database_open_failure_is_not_reported_as_port_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            invalid_parent = Path(temporary_directory) / "not-a-directory"
            invalid_parent.write_text("occupied", encoding="utf-8")
            database_path = invalid_parent / "wander.db"

            with self.assertRaisesRegex(
                DesktopStartupError,
                rf"could not open its database at {re.escape(str(database_path))}",
            ):
                DesktopHost.create("127.0.0.1", 0, database_path)

    def test_second_host_is_rejected_before_it_can_open_the_same_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "wander.db"
            first = DesktopHost.create("127.0.0.1", 0, database_path, mcp_port=0)
            try:
                with self.assertRaisesRegex(DesktopStartupError, "already running"):
                    DesktopHost.create("127.0.0.1", 0, database_path, mcp_port=0)
            finally:
                first.stop()

            replacement = DesktopHost.create("127.0.0.1", 0, database_path, mcp_port=0)
            replacement.stop()

    def test_schema_backup_precedes_store_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "wander.db"
            backups = root / "safe-copies"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
                connection.execute("INSERT INTO sentinel VALUES ('before migration')")
                connection.execute("PRAGMA user_version = 3")
                connection.commit()

            host = DesktopHost.create(
                "127.0.0.1",
                0,
                database_path,
                mcp_port=0,
                backups_directory=backups,
            )
            host.stop()

            created_backups = list(backups.glob("pre-schema-3-to-4-*.db"))
            self.assertEqual(1, len(created_backups))
            with closing(sqlite3.connect(created_backups[0])) as connection:
                self.assertEqual(3, connection.execute("PRAGMA user_version").fetchone()[0])
                self.assertEqual(
                    "before migration",
                    connection.execute("SELECT value FROM sentinel").fetchone()[0],
                )
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(4, connection.execute("PRAGMA user_version").fetchone()[0])

    def test_newer_schema_is_rejected_without_mutation_and_releases_the_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "future.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE future_data (value TEXT NOT NULL)")
                connection.execute("INSERT INTO future_data VALUES ('preserve me')")
                connection.execute("PRAGMA user_version = 5")
                connection.commit()

            for _ in range(2):
                with self.assertRaisesRegex(DesktopStartupError, "newer than"):
                    DesktopHost.create("127.0.0.1", 0, database_path, mcp_port=0)

            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(5, connection.execute("PRAGMA user_version").fetchone()[0])
                self.assertEqual(
                    "preserve me",
                    connection.execute("SELECT value FROM future_data").fetchone()[0],
                )

    def test_native_window_wraps_the_in_process_server(self) -> None:
        calls: list[tuple[str, object]] = []

        def create_window(title: str, url: str, **options: object) -> None:
            calls.append(("window", (title, url, options)))

        def start(**options: object) -> None:
            calls.append(("start", options))

        fake_webview = types.SimpleNamespace(create_window=create_window, start=start)
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_desktop(
                "127.0.0.1",
                0,
                Path(temporary_directory) / "wander.db",
                mcp_port=0,
                webview_module=fake_webview,
            )

        self.assertEqual("Wander", calls[0][1][0])
        self.assertRegex(calls[0][1][1], r"^http://127\.0\.0\.1:\d+$")
        self.assertEqual(("start", {"gui": "edgechromium"}), calls[1])


if __name__ == "__main__":
    unittest.main()

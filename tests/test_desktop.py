from __future__ import annotations

import json
import re
import socket
import tempfile
import types
import unittest
from pathlib import Path
from urllib.request import urlopen

from wander.desktop import DesktopHost, DesktopStartupError, run_desktop


class DesktopHostTests(unittest.TestCase):
    def test_server_becomes_healthy_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            host = DesktopHost.create(
                "127.0.0.1",
                0,
                Path(temporary_directory) / "wander.db",
            )
            host.start()
            thread = host.thread
            with urlopen(f"{host.local_url}/api/health", timeout=1) as response:
                health = json.load(response)
            self.assertEqual("ok", health["status"])
            self.assertTrue(thread and thread.is_alive())

            host.stop()

            self.assertFalse(thread and thread.is_alive())
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", host.server.server_port), timeout=0.2)

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
                )

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
                webview_module=fake_webview,
            )

        self.assertEqual("Wander", calls[0][1][0])
        self.assertRegex(calls[0][1][1], r"^http://127\.0\.0\.1:\d+$")
        self.assertEqual(("start", {"gui": "edgechromium"}), calls[1])


if __name__ == "__main__":
    unittest.main()

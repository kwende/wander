from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.error import URLError
from urllib.request import urlopen

import anyio
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from wander.mcp_server import configure_mcp_server, release_mcp_server
from wander.paths import backup_directory_for_database, default_database_path
from wander.persistence import (
    InstanceAlreadyRunningError,
    PersistenceError,
    SingleInstanceLock,
    backup_before_schema_migration,
    check_database_compatibility,
    import_legacy_database,
)
from wander.store import SCHEMA_VERSION, WanderStore
from wander.web import create_server


class DesktopStartupError(RuntimeError):
    pass


@dataclass
class DesktopHost:
    server: ThreadingHTTPServer
    store: WanderStore
    mcp_transport: uvicorn.Server
    mcp_port: int
    instance_lock: SingleInstanceLock
    thread: threading.Thread | None = None
    mcp_thread: threading.Thread | None = None
    mcp_error: BaseException | None = field(default=None, init=False)

    @classmethod
    def create(
        cls,
        host: str,
        port: int,
        database_path: Path,
        share_base_url: str | None = None,
        mcp_port: int = 8766,
        backups_directory: Path | None = None,
    ) -> DesktopHost:
        instance_lock = SingleInstanceLock(database_path)
        try:
            instance_lock.acquire()
        except InstanceAlreadyRunningError as error:
            raise DesktopStartupError(
                "Wander is already running with this ledger. Use the existing window."
            ) from error
        try:
            backup_before_schema_migration(
                database_path,
                backups_directory or backup_directory_for_database(database_path),
                SCHEMA_VERSION,
            )
            store = WanderStore(database_path)
        except (OSError, sqlite3.Error, PersistenceError) as error:
            instance_lock.release()
            raise DesktopStartupError(
                f"Wander could not open its database at {database_path}: {error}"
            ) from error
        try:
            mcp = configure_mcp_server(store)
        except Exception:
            instance_lock.release()
            raise
        server: ThreadingHTTPServer | None = None
        try:
            server = create_server(
                host,
                port,
                store,
                share_base_url,
            )
            mcp_app = mcp.streamable_http_app(
                host="127.0.0.1",
                streamable_http_path="/mcp",
                json_response=True,
                stateless_http=True,
            )
            mcp_transport = uvicorn.Server(
                uvicorn.Config(
                    mcp_app,
                    host="127.0.0.1",
                    port=mcp_port,
                    log_level="warning",
                    access_log=False,
                    log_config=None,
                )
            )
        except OSError as error:
            if server is not None:
                server.server_close()
            release_mcp_server(store)
            instance_lock.release()
            raise DesktopStartupError(
                f"Wander could not configure its listeners: {error}. "
                "Another Wander instance or application may already own that address."
            ) from error
        except Exception:
            if server is not None:
                server.server_close()
            release_mcp_server(store)
            instance_lock.release()
            raise
        return cls(server, store, mcp_transport, mcp_port, instance_lock)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def mcp_url(self) -> str:
        return f"http://127.0.0.1:{self.mcp_port}/mcp"

    def _run_mcp(self) -> None:
        try:
            self.mcp_transport.run()
        except BaseException as error:
            self.mcp_error = error

    async def _probe_mcp(self) -> None:
        async with streamable_http_client(self.mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

    def _resolve_mcp_port(self) -> None:
        if self.mcp_port != 0 or not self.mcp_transport.servers:
            return
        sockets = self.mcp_transport.servers[0].sockets or []
        if sockets:
            self.mcp_port = int(sockets[0].getsockname()[1])

    def start(self, timeout_seconds: float = 5.0) -> None:
        if self.thread is not None or self.mcp_thread is not None:
            raise RuntimeError("Wander desktop server is already started")
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="wander-desktop-http",
            daemon=True,
        )
        self.mcp_thread = threading.Thread(
            target=self._run_mcp,
            name="wander-desktop-mcp",
            daemon=True,
        )
        self.thread.start()
        self.mcp_thread.start()
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        dashboard_healthy = False
        mcp_healthy = False
        while time.monotonic() < deadline:
            if self.mcp_error is not None:
                break
            if not dashboard_healthy:
                try:
                    with urlopen(f"{self.local_url}/api/health", timeout=0.5) as response:
                        health = json.load(response)
                    dashboard_healthy = health.get("status") == "ok"
                except (OSError, URLError, ValueError) as error:
                    last_error = error
            if self.mcp_transport.started and not mcp_healthy:
                self._resolve_mcp_port()
                try:
                    anyio.run(self._probe_mcp)
                    mcp_healthy = True
                except Exception as error:
                    last_error = error
            if dashboard_healthy and mcp_healthy:
                return
            time.sleep(0.05)
        self.stop()
        if self.mcp_error is not None:
            raise DesktopStartupError(
                f"Wander's MCP server could not listen on 127.0.0.1:{self.mcp_port}"
            ) from self.mcp_error
        raise DesktopStartupError(
            "Wander's dashboard and MCP server did not become healthy"
        ) from last_error

    def stop(self) -> None:
        mcp_thread = self.mcp_thread
        if mcp_thread is not None and mcp_thread.is_alive():
            self.mcp_transport.should_exit = True
            mcp_thread.join(timeout=5)
            if mcp_thread.is_alive():
                self.mcp_transport.force_exit = True
                mcp_thread.join(timeout=5)
        mcp_stopped = mcp_thread is None or not mcp_thread.is_alive()
        self.mcp_thread = None
        thread = self.thread
        if thread is not None and thread.is_alive():
            self.server.shutdown()
            thread.join(timeout=5)
        self.server.server_close()
        self.thread = None
        if mcp_stopped:
            release_mcp_server(self.store)
            self.instance_lock.release()
            return
        raise RuntimeError("Wander's MCP server did not stop cleanly")


def run_desktop(
    host: str,
    port: int,
    database_path: Path,
    share_base_url: str | None = None,
    mcp_port: int = 8766,
    backups_directory: Path | None = None,
    webview_module: ModuleType | None = None,
) -> None:
    desktop = DesktopHost.create(
        host,
        port,
        database_path,
        share_base_url,
        mcp_port,
        backups_directory,
    )
    try:
        desktop.start()
        if webview_module is None:
            import webview as webview_module

        webview_module.create_window(
            "Wander",
            desktop.local_url,
            width=1200,
            height=850,
            min_size=(800, 600),
        )
        webview_module.start(gui="edgechromium")
    finally:
        desktop.stop()


def show_startup_error(message: str) -> None:
    print(message, file=sys.stderr)
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "Wander could not start", 0x10)
    except (AttributeError, OSError):
        pass


def _write_diagnostic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_self_test() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="wander-self-test-") as temporary_directory:
        root = Path(temporary_directory)
        host = DesktopHost.create(
            "127.0.0.1",
            0,
            root / "wander.db",
            mcp_port=0,
            backups_directory=root / "backups",
        )
        dashboard_port = host.server.server_port
        try:
            host.start()
            created = host.store.create_journey(
                "packaged-self-test",
                "Packaged self-test",
                "Verify the bundled runtime without touching user data.",
            )
            with urlopen(f"{host.local_url}/api/health", timeout=1) as response:
                health = json.load(response)
            with urlopen(
                f"{host.local_url}/api/journeys/packaged-self-test", timeout=1
            ) as response:
                journey = json.load(response)
            mcp_port = host.mcp_port
        finally:
            host.stop()
        endpoints_closed = True
        for port in (dashboard_port, mcp_port):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    endpoints_closed = False
            except OSError:
                pass
        if health.get("status") != "ok" or journey.get("version") != created["version"]:
            raise DesktopStartupError("Packaged self-test observed inconsistent application state")
        if not endpoints_closed:
            raise DesktopStartupError("Packaged self-test did not release both listeners")
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "dashboard_mode": health.get("mode"),
            "mcp_host": "127.0.0.1",
            "endpoints_closed": endpoints_closed,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open Wander as a desktop application")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mcp-port", type=int, default=8766)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--backups-directory", type=Path)
    parser.add_argument("--share-base-url")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--check-database", type=Path)
    parser.add_argument("--import-legacy-database", type=Path)
    parser.add_argument("--diagnostic-output", type=Path)
    arguments = parser.parse_args(argv)
    diagnostic_modes = (
        bool(arguments.self_test),
        arguments.check_database is not None,
        arguments.import_legacy_database is not None,
    )
    if sum(diagnostic_modes) > 1:
        parser.error("diagnostic modes are mutually exclusive")
    if any(diagnostic_modes) and not arguments.diagnostic_output:
        parser.error("diagnostic modes require --diagnostic-output")
    if arguments.self_test:
        try:
            report = run_self_test()
        except Exception as error:
            _write_diagnostic(
                arguments.diagnostic_output,
                {"status": "error", "error": str(error), "traceback": traceback.format_exc()},
            )
            return 1
        _write_diagnostic(arguments.diagnostic_output, report)
        return 0
    if arguments.check_database:
        try:
            report = check_database_compatibility(arguments.check_database, SCHEMA_VERSION)
        except Exception as error:
            _write_diagnostic(
                arguments.diagnostic_output,
                {"status": "error", "error": str(error), "traceback": traceback.format_exc()},
            )
            return 1
        _write_diagnostic(arguments.diagnostic_output, {"status": "ok", **report})
        return 0 if report["compatible"] else 2
    database_path = arguments.database or default_database_path()
    if arguments.import_legacy_database:
        try:
            receipt = import_legacy_database(
                arguments.import_legacy_database,
                database_path,
                arguments.backups_directory
                or backup_directory_for_database(database_path),
            )
        except Exception as error:
            _write_diagnostic(
                arguments.diagnostic_output,
                {"status": "error", "error": str(error), "traceback": traceback.format_exc()},
            )
            return 1
        _write_diagnostic(
            arguments.diagnostic_output,
            {"status": "ok", "operation": "legacy-import", **asdict(receipt)},
        )
        return 0
    try:
        run_desktop(
            arguments.host,
            arguments.port,
            database_path,
            arguments.share_base_url,
            arguments.mcp_port,
            arguments.backups_directory or backup_directory_for_database(database_path),
        )
    except Exception as error:
        show_startup_error(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.error import URLError
from urllib.request import urlopen

from wander.paths import default_database_path
from wander.store import WanderStore
from wander.web import create_server


class DesktopStartupError(RuntimeError):
    pass


@dataclass
class DesktopHost:
    server: ThreadingHTTPServer
    thread: threading.Thread | None = None

    @classmethod
    def create(
        cls,
        host: str,
        port: int,
        database_path: Path,
        share_base_url: str | None = None,
    ) -> DesktopHost:
        try:
            store = WanderStore(database_path)
        except OSError as error:
            raise DesktopStartupError(
                f"Wander could not open its database at {database_path}: {error}"
            ) from error
        try:
            server = create_server(
                host,
                port,
                store,
                share_base_url,
            )
        except OSError as error:
            raise DesktopStartupError(
                f"Wander could not listen on {host}:{port}: {error}. "
                "Another Wander instance or application may already own that address."
            ) from error
        return cls(server)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self, timeout_seconds: float = 5.0) -> None:
        if self.thread is not None:
            raise RuntimeError("Wander desktop server is already started")
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="wander-desktop-http",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urlopen(f"{self.local_url}/api/health", timeout=0.5) as response:
                    health = json.load(response)
                if health.get("status") == "ok":
                    return
            except (OSError, URLError, ValueError) as error:
                last_error = error
            time.sleep(0.05)
        self.stop()
        raise DesktopStartupError("Wander's dashboard did not become healthy") from last_error

    def stop(self) -> None:
        thread = self.thread
        if thread is not None and thread.is_alive():
            self.server.shutdown()
            thread.join(timeout=5)
        self.server.server_close()
        self.thread = None


def run_desktop(
    host: str,
    port: int,
    database_path: Path,
    share_base_url: str | None = None,
    webview_module: ModuleType | None = None,
) -> None:
    desktop = DesktopHost.create(host, port, database_path, share_base_url)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open Wander as a desktop application")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=default_database_path())
    parser.add_argument("--share-base-url")
    arguments = parser.parse_args(argv)
    try:
        run_desktop(
            arguments.host,
            arguments.port,
            arguments.database,
            arguments.share_base_url,
        )
    except Exception as error:
        show_startup_error(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from wander.store import NotFoundError, ValidationError, WanderStore


STATIC_ROOT = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class DashboardHandler(BaseHTTPRequestHandler):
    store: WanderStore

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, value: object) -> None:
        self._send_bytes(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "The dashboard is read-only. Mutate Wander through its MCP tools."},
        )

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_POST()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_POST()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_POST()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok", "mode": "read-only"})
                return
            if path == "/api/projects":
                self._send_json(HTTPStatus.OK, self.store.list_projects())
                return
            if path.startswith("/api/projects/"):
                project = path.removeprefix("/api/projects/")
                if not project or "/" in project:
                    raise ValidationError("Invalid project path")
                self._send_json(HTTPStatus.OK, self.store.get_project(project))
                return
            self._serve_static(path)
        except NotFoundError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
        except ValidationError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in candidate.parents and candidate != STATIC_ROOT.resolve():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        self._send_bytes(
            HTTPStatus.OK,
            candidate.read_bytes(),
            CONTENT_TYPES.get(candidate.suffix, "application/octet-stream"),
        )


def create_server(host: str, port: int, store: WanderStore) -> ThreadingHTTPServer:
    handler = type("ConfiguredDashboardHandler", (DashboardHandler,), {"store": store})
    return ThreadingHTTPServer((host, port), handler)


def default_database_path() -> Path:
    configured = os.environ.get("WANDER_DB")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "wander.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the read-only Wander dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=default_database_path())
    arguments = parser.parse_args()
    server = create_server(arguments.host, arguments.port, WanderStore(arguments.database))
    print(f"Wander dashboard: http://{arguments.host}:{server.server_port}")
    print("The HTTP surface is read-only; writes are available only through MCP.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()


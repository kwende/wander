from __future__ import annotations

import ipaddress
import json
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from wander.store import ConflictError, NotFoundError, ValidationError, WanderStore


STATIC_ROOT = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
MAX_JSON_BODY_BYTES = 1_000_000


def is_loopback_address(value: str) -> bool:
    normalized = value.removeprefix("::ffff:")
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


class DashboardHandler(BaseHTTPRequestHandler):
    store: WanderStore
    share_base_url: str

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

    def _require_local_jaunts(self) -> None:
        if not is_loopback_address(self.client_address[0]):
            raise PermissionError("Jaunts are available only from localhost")

    def _read_json_object(self) -> dict[str, object]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValidationError("Invalid Content-Length") from error
        if content_length <= 0 or content_length > MAX_JSON_BODY_BYTES:
            raise ValidationError("JSON body is required and must be at most 1 MB")
        try:
            value = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValidationError("Request body must be valid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("Request body must be a JSON object")
        return value

    @staticmethod
    def _expected_version(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValidationError("expected_version must be a positive integer")
        return value

    def _send_api_error(self, error: Exception) -> None:
        if isinstance(error, PermissionError):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(error)})
        elif isinstance(error, NotFoundError):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
        elif isinstance(error, ConflictError):
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        elif isinstance(error, ValidationError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        else:
            raise error

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/jaunts":
                self._require_local_jaunts()
                body = self._read_json_object()
                title = body.get("title")
                notes = body.get("notes", "")
                if not isinstance(title, str) or not isinstance(notes, str):
                    raise ValidationError("title and notes must be strings")
                mutation = self.store.create_jaunt(title, notes)
                self._send_json(
                    HTTPStatus.CREATED,
                    self.store.get_jaunt(mutation["jaunt"]),
                )
                return
            if path.startswith("/api/jaunts/"):
                self._require_local_jaunts()
                jaunt_id, action = self._parse_jaunt_action_path(path)
                body = self._read_json_object()
                expected_version = self._expected_version(body.get("expected_version"))
                summary = body.get("summary")
                if summary is not None and not isinstance(summary, str):
                    raise ValidationError("summary must be a string")
                if action == "complete":
                    self.store.complete_jaunt(
                        jaunt_id,
                        expected_version,
                        summary or "Completed jaunt",
                    )
                elif action == "reopen":
                    self.store.reopen_jaunt(
                        jaunt_id,
                        expected_version,
                        summary or "Reopened jaunt",
                    )
                else:
                    raise ValidationError("Unknown jaunt action")
                self._send_json(HTTPStatus.OK, self.store.get_jaunt(jaunt_id))
                return
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "Journey dashboard writes are available only through MCP."},
            )
        except (ConflictError, NotFoundError, PermissionError, ValidationError) as error:
            self._send_api_error(error)

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/jaunts/") and path.count("/") == 3:
                self._require_local_jaunts()
                jaunt_id = path.removeprefix("/api/jaunts/")
                if not jaunt_id:
                    raise ValidationError("Invalid jaunt path")
                body = self._read_json_object()
                expected_version = self._expected_version(body.get("expected_version"))
                title = body.get("title")
                notes = body.get("notes")
                if title is not None and not isinstance(title, str):
                    raise ValidationError("title must be a string")
                if notes is not None and not isinstance(notes, str):
                    raise ValidationError("notes must be a string")
                self.store.update_jaunt(
                    jaunt_id,
                    expected_version,
                    title=title,
                    notes=notes,
                )
                self._send_json(HTTPStatus.OK, self.store.get_jaunt(jaunt_id))
                return
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "Journey dashboard writes are available only through MCP."},
            )
        except (ConflictError, NotFoundError, PermissionError, ValidationError) as error:
            self._send_api_error(error)

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Method not allowed"})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed_request = urlparse(self.path)
        path = unquote(parsed_request.path)
        try:
            if path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "mode": "read-only",
                        "jaunts_mode": (
                            "local-authoring"
                            if is_loopback_address(self.client_address[0])
                            else "unavailable"
                        ),
                        "share_base_url": self.share_base_url,
                    },
                )
                return
            if path == "/api/jaunts":
                self._require_local_jaunts()
                status_values = parse_qs(parsed_request.query).get("status", [])
                if len(status_values) > 1:
                    raise ValidationError("status may be specified only once")
                status = status_values[0] if status_values else None
                self._send_json(HTTPStatus.OK, self.store.list_jaunts(status))
                return
            if path.startswith("/api/jaunts/"):
                self._require_local_jaunts()
                jaunt_id = path.removeprefix("/api/jaunts/")
                if not jaunt_id or "/" in jaunt_id:
                    raise ValidationError("Invalid jaunt path")
                self._send_json(HTTPStatus.OK, self.store.get_jaunt(jaunt_id))
                return
            if path in {"/api/journeys", "/api/projects"}:
                self._send_json(HTTPStatus.OK, self.store.list_journeys())
                return
            journey_prefix = next(
                (
                    prefix
                    for prefix in ("/api/journeys/", "/api/projects/")
                    if path.startswith(prefix)
                ),
                None,
            )
            if journey_prefix:
                journey = path.removeprefix(journey_prefix)
                if not journey or "/" in journey:
                    raise ValidationError("Invalid journey path")
                self._send_json(HTTPStatus.OK, self.store.get_journey(journey))
                return
            self._serve_static(path)
        except (NotFoundError, PermissionError, ValidationError) as error:
            self._send_api_error(error)

    @staticmethod
    def _parse_jaunt_action_path(path: str) -> tuple[str, str]:
        parts = path.removeprefix("/api/jaunts/").split("/")
        if len(parts) != 2 or not all(parts):
            raise ValidationError("Invalid jaunt action path")
        return parts[0], parts[1]

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


def detect_lan_address() -> str:
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        for address in addresses:
            if address and not address.startswith(("127.", "169.254.")):
                return address
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as candidate:
            candidate.connect(("8.8.8.8", 80))
            address = candidate.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return "127.0.0.1"


def normalize_share_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(
            "Share base URL must be an absolute http(s) origin without credentials, path, query, or fragment"
        )
    return value.rstrip("/")


def create_server(
    host: str,
    port: int,
    store: WanderStore,
    share_base_url: str | None = None,
) -> ThreadingHTTPServer:
    handler = type("ConfiguredDashboardHandler", (DashboardHandler,), {"store": store})
    server = ThreadingHTTPServer((host, port), handler)
    advertised_host = detect_lan_address() if host in {"0.0.0.0", "::"} else host
    handler.share_base_url = normalize_share_base_url(
        share_base_url or f"http://{advertised_host}:{server.server_port}"
    )
    return server


def main() -> None:
    raise RuntimeError(
        "The Wander dashboard is hosted by the Wander desktop application. "
        "Start Wander instead."
    )


if __name__ == "__main__":
    main()


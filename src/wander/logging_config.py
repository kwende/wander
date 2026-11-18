from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any


LOG_NAME = "wander.jsonl"
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 10


@dataclass(frozen=True)
class LoggingRuntime:
    path: str
    run_id: str


class RuntimeContextFilter(logging.Filter):
    def __init__(self, run_id: str, app_version: str):
        super().__init__()
        self.run_id = run_id
        self.app_version = app_version

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        record.app_version = self.app_version
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event_name", record.getMessage()),
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "process_id": record.process,
            "thread": record.threadName,
            "app_version": getattr(record, "app_version", None),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            document.update(fields)
        if record.exc_info:
            document["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def event_fields(event_name: str, **fields: object) -> dict[str, object]:
    return {"event_name": event_name, "event_fields": fields}


def configure_logging(
    logs_directory: str | Path,
    app_version: str,
    *,
    run_id: str | None = None,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    stderr: bool = True,
) -> LoggingRuntime:
    logs = Path(logs_directory).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / LOG_NAME
    resolved_run_id = run_id or str(uuid.uuid4())
    logger = logging.getLogger("wander")
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    context = RuntimeContextFilter(resolved_run_id, app_version)
    formatter = JsonFormatter()
    file_handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.addFilter(context)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.addFilter(context)
        stderr_handler.setFormatter(formatter)
        logger.addHandler(stderr_handler)
    _install_exception_hooks(logger)
    return LoggingRuntime(path=str(path), run_id=resolved_run_id)


def shutdown_logging() -> None:
    logger = logging.getLogger("wander")
    for handler in logger.handlers:
        handler.flush()
        handler.close()
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False


def _install_exception_hooks(logger: logging.Logger) -> None:
    def process_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        exception_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            sys.__excepthook__(exception_type, exception, exception_traceback)
            return
        logger.critical(
            "process.unhandled_exception",
            exc_info=(exception_type, exception, exception_traceback),
            extra=event_fields("process.unhandled_exception"),
        )

    def thread_exception(arguments: threading.ExceptHookArgs) -> None:
        if issubclass(arguments.exc_type, SystemExit):
            return
        logger.critical(
            "thread.unhandled_exception",
            exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback),
            extra=event_fields(
                "thread.unhandled_exception",
                failed_thread=arguments.thread.name if arguments.thread else None,
            ),
        )

    sys.excepthook = process_exception
    threading.excepthook = thread_exception


_package_logger = logging.getLogger("wander")
_package_logger.addHandler(logging.NullHandler())
_package_logger.propagate = False

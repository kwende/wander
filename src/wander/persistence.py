from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class PersistenceError(RuntimeError):
    pass


class InstanceAlreadyRunningError(PersistenceError):
    pass


class SingleInstanceLock:
    """Hold one process-level lease for a database path without opening SQLite."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()
        identity = os.path.normcase(str(self.database_path)).encode("utf-8")
        self.name = f"Wander-{hashlib.sha256(identity).hexdigest()[:32]}"
        self._handle: int | None = None
        self._lock_file: Any | None = None

    def acquire(self) -> SingleInstanceLock:
        if self._handle is not None or self._lock_file is not None:
            raise RuntimeError("Wander instance lock is already acquired")
        if sys.platform == "win32":
            self._acquire_windows_mutex()
        else:
            self._acquire_file_lock()
        return self

    def _acquire_windows_mutex(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool

        ctypes.set_last_error(0)
        handle = create_mutex(None, False, f"Global\\{self.name}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            close_handle(handle)
            raise InstanceAlreadyRunningError(
                f"Wander is already using {self.database_path}"
            )
        self._handle = int(handle)

    def _acquire_file_lock(self) -> None:
        import fcntl

        lock_path = self.database_path.with_suffix(self.database_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock_file.close()
            raise InstanceAlreadyRunningError(
                f"Wander is already using {self.database_path}"
            ) from error
        self._lock_file = lock_file

    def release(self) -> None:
        if self._handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if not kernel32.CloseHandle(ctypes.c_void_p(self._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None
        if self._lock_file is not None:
            import fcntl

            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self) -> SingleInstanceLock:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass(frozen=True)
class ImportReceipt:
    source: str
    destination: str
    backup: str
    source_schema: int
    destination_schema: int
    backup_sha256: str
    inventory: dict[str, Any]
    migrated_at: str


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _temporary_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")


def _remove_sqlite_files(database_path: Path) -> None:
    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
        Path(f"{database_path}-journal"),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _assert_integrity(connection: sqlite3.Connection, label: str) -> None:
    results = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
    if results != ["ok"]:
        raise PersistenceError(f"{label} failed SQLite integrity_check: {results}")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise PersistenceError(
            f"{label} failed SQLite foreign_key_check: {len(foreign_key_errors)} error(s)"
        )


def _schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def check_database_compatibility(
    database_path: str | Path,
    supported_schema_version: int,
) -> dict[str, Any]:
    database = Path(database_path).resolve()
    with SingleInstanceLock(database):
        if not database.exists():
            return {
                "database": str(database),
                "exists": False,
                "schema_version": None,
                "supported_schema": supported_schema_version,
                "compatible": True,
            }
        with closing(_open_read_only(database)) as connection:
            _assert_integrity(connection, str(database))
            schema_version = _schema_version(connection)
        return {
            "database": str(database),
            "exists": True,
            "schema_version": schema_version,
            "supported_schema": supported_schema_version,
            "compatible": schema_version <= supported_schema_version,
        }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _inventory_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    if not _table_exists(connection, table):
        return {"count": 0, "sha256": hashlib.sha256().hexdigest()}
    selected = ", ".join(f'"{column}"' for column in columns)
    order = ", ".join(f'"{column}"' for column in columns)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(f'SELECT {selected} FROM "{table}" ORDER BY {order}'):
        digest.update(
            json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def database_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    journey_table = "journeys" if _table_exists(connection, "journeys") else "projects"
    link_table = (
        "journey_links" if _table_exists(connection, "journey_links") else "project_links"
    )
    journey_key = "journey_id" if journey_table == "journeys" else "project_id"
    return {
        "journeys": _inventory_rows(
            connection, journey_table, ("id", "slug", "status", "version")
        ),
        "work_items": _inventory_rows(
            connection, "work_items", ("id", journey_key, "parent_id", "status")
        ),
        "journey_links": _inventory_rows(
            connection, link_table, ("id", journey_key)
        ),
        "work_item_links": _inventory_rows(
            connection, "work_item_links", ("id", journey_key, "work_item_id")
        ),
        "thread_references": _inventory_rows(
            connection, "thread_references", ("id", journey_key)
        ),
        "checkpoints": _inventory_rows(
            connection, "checkpoints", ("id", journey_key)
        ),
        "events": _inventory_rows(
            connection,
            "events",
            ("id", journey_key, "before_version", "after_version", "operation"),
        ),
        "jaunts": _inventory_rows(
            connection, "jaunts", ("id", "status", "version")
        ),
        "jaunt_events": _inventory_rows(
            connection,
            "jaunt_events",
            ("id", "jaunt_id", "before_version", "after_version", "operation"),
        ),
    }


def _backup_database(source: Path, destination: Path) -> None:
    if destination.exists():
        raise PersistenceError(f"Refusing to overwrite SQLite backup: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination)
    try:
        with closing(_open_read_only(source)) as source_connection:
            _assert_integrity(source_connection, str(source))
            with closing(sqlite3.connect(temporary)) as destination_connection:
                source_connection.backup(destination_connection)
                destination_connection.commit()
        with closing(_open_read_only(temporary)) as backup_connection:
            _assert_integrity(backup_connection, str(destination))
        os.replace(temporary, destination)
    except BaseException:
        _remove_sqlite_files(temporary)
        raise


def _finalize_staged_database(database_path: Path) -> None:
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        if str(journal_mode).casefold() != "delete":
            raise PersistenceError(
                f"Could not finalize staged SQLite journal at {database_path}"
            )
        connection.commit()
    companions = [
        path
        for path in (Path(f"{database_path}-wal"), Path(f"{database_path}-shm"))
        if path.exists()
    ]
    if companions:
        raise PersistenceError(f"Staged SQLite database retained companions: {companions}")


def backup_before_schema_migration(
    database_path: str | Path,
    backups_directory: str | Path,
    target_schema_version: int,
) -> Path | None:
    database = Path(database_path).resolve()
    if not database.exists() or database.stat().st_size == 0:
        return None
    with closing(_open_read_only(database)) as connection:
        current_schema_version = _schema_version(connection)
    if current_schema_version > target_schema_version:
        raise PersistenceError(
            f"Wander database schema {current_schema_version} is newer than "
            f"this application supports ({target_schema_version})"
        )
    if current_schema_version == target_schema_version:
        return None
    backup = Path(backups_directory).resolve() / (
        f"pre-schema-{current_schema_version}-to-{target_schema_version}-{_timestamp()}.db"
    )
    _backup_database(database, backup)
    return backup


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_receipt(receipt: ImportReceipt, receipt_path: Path) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(receipt_path)
    try:
        temporary.write_text(
            json.dumps(asdict(receipt), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, receipt_path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _import_legacy_database(
    source_path: str | Path,
    destination_path: str | Path,
    backups_directory: str | Path,
) -> ImportReceipt:
    """Import a stopped ledger while Wander exclusively owns both paths."""

    from wander.store import SCHEMA_VERSION, WanderStore

    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    backups = Path(backups_directory).resolve()
    if source == destination:
        raise PersistenceError("Legacy source and destination must be different databases")
    if not source.is_file():
        raise PersistenceError(f"Legacy Wander database does not exist: {source}")
    if destination.exists():
        raise PersistenceError(f"Refusing to overwrite Wander database: {destination}")

    locks = sorted((source, destination), key=lambda path: os.path.normcase(str(path)))
    with ExitStack() as stack:
        for path in locks:
            stack.enter_context(SingleInstanceLock(path))

        with closing(_open_read_only(source)) as source_connection:
            _assert_integrity(source_connection, str(source))
            source_schema = _schema_version(source_connection)
            if source_schema > SCHEMA_VERSION:
                raise PersistenceError(
                    f"Legacy database schema {source_schema} is newer than "
                    f"this application supports ({SCHEMA_VERSION})"
                )
            source_inventory = database_inventory(source_connection)

        backup = backups / f"first-desktop-import-{_timestamp()}.db"
        _backup_database(source, backup)
        with closing(_open_read_only(backup)) as backup_connection:
            if database_inventory(backup_connection) != source_inventory:
                raise PersistenceError("The first-import backup does not match the source ledger")

        staged_destination = _temporary_path(destination)
        receipt_path = backup.with_suffix(".json")
        try:
            _backup_database(backup, staged_destination)
            WanderStore(staged_destination)
            _finalize_staged_database(staged_destination)
            with closing(_open_read_only(staged_destination)) as migrated_connection:
                _assert_integrity(migrated_connection, str(staged_destination))
                destination_schema = _schema_version(migrated_connection)
                destination_inventory = database_inventory(migrated_connection)
            if destination_schema != SCHEMA_VERSION:
                raise PersistenceError(
                    f"Migrated ledger schema is {destination_schema}; expected {SCHEMA_VERSION}"
                )
            if destination_inventory != source_inventory:
                raise PersistenceError("Migrated ledger inventory does not match the source")
            receipt = ImportReceipt(
                source=str(source),
                destination=str(destination),
                backup=str(backup),
                source_schema=source_schema,
                destination_schema=destination_schema,
                backup_sha256=_sha256(backup),
                inventory=source_inventory,
                migrated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            _write_receipt(receipt, receipt_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(staged_destination, destination)
            except BaseException:
                try:
                    receipt_path.unlink()
                except FileNotFoundError:
                    pass
                raise
        except BaseException:
            _remove_sqlite_files(staged_destination)
            raise
        return receipt


def import_legacy_database(
    source_path: str | Path,
    destination_path: str | Path,
    backups_directory: str | Path,
) -> ImportReceipt:
    try:
        return _import_legacy_database(source_path, destination_path, backups_directory)
    except PersistenceError:
        raise
    except sqlite3.Error as error:
        raise PersistenceError(f"SQLite migration failed: {error}") from error
    except Exception as error:
        from wander.store import WanderError

        if isinstance(error, WanderError):
            raise PersistenceError(f"Wander schema migration failed: {error}") from error
        raise

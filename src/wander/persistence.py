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
    ledger_id: str
    migrated_at: str


@dataclass(frozen=True)
class LedgerIdentityReceipt:
    database: str
    ledger_id: str
    registered_at: str


@dataclass(frozen=True)
class SnapshotManifest:
    snapshot_file: str
    ledger_id: str
    schema_version: int
    created_at: str
    size_bytes: int
    sha256: str
    inventory: dict[str, Any]


@dataclass(frozen=True)
class RestoreSource:
    kind: str
    path: str
    ledger_id: str | None
    schema_version: int
    sha256: str
    inventory: dict[str, Any]
    expected_destination: str | None = None


@dataclass(frozen=True)
class RestoreSafetyReceipt:
    kind: str
    backup_file: str
    database: str
    ledger_id: str | None
    schema_version: int
    backup_sha256: str
    inventory: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class RestoreReceipt:
    source: str
    source_kind: str
    destination: str
    safety_backup: str | None
    previous_ledger_id: str | None
    restored_ledger_id: str
    restored_schema: int
    inventory: dict[str, Any]
    restored_at: str


DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 60 * 60
DEFAULT_SNAPSHOT_RETENTION = 24 * 7
SNAPSHOT_PREFIX = "ledger-snapshot-"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _temporary_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")


def identity_path_for_database(database_path: str | Path) -> Path:
    database = Path(database_path).resolve()
    return database.with_suffix(".identity.json")


def new_ledger_authorization_path(database_path: str | Path) -> Path:
    database = Path(database_path).resolve()
    return database.with_suffix(".new-ledger.json")


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


def _write_json_atomic(payload: object, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination)
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_receipt(receipt: ImportReceipt, receipt_path: Path) -> None:
    _write_json_atomic(asdict(receipt), receipt_path)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise PersistenceError(f"Could not read Wander identity document {path}: {error}") from error
    if not isinstance(payload, dict):
        raise PersistenceError(f"Wander identity document is not an object: {path}")
    return payload


def _ledger_id(connection: sqlite3.Connection) -> str:
    if not _table_exists(connection, "ledger_metadata"):
        raise PersistenceError("Wander ledger metadata is missing")
    row = connection.execute(
        "SELECT ledger_id FROM ledger_metadata WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise PersistenceError("Wander ledger identity is missing")
    return str(row[0])


def _optional_ledger_id(connection: sqlite3.Connection) -> str | None:
    if not _table_exists(connection, "ledger_metadata"):
        return None
    row = connection.execute(
        "SELECT ledger_id FROM ledger_metadata WHERE singleton = 1"
    ).fetchone()
    return str(row[0]) if row is not None else None


def _snapshot_manifest_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_suffix(".json")


def _load_snapshot_manifest(manifest_path: Path) -> SnapshotManifest:
    document = _read_json_object(manifest_path)
    try:
        inventory = document["inventory"]
        if not isinstance(inventory, dict):
            raise TypeError("inventory is not an object")
        return SnapshotManifest(
            snapshot_file=str(document["snapshot_file"]),
            ledger_id=str(document["ledger_id"]),
            schema_version=int(document["schema_version"]),
            created_at=str(document["created_at"]),
            size_bytes=int(document["size_bytes"]),
            sha256=str(document["sha256"]),
            inventory=inventory,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceError(
            f"Invalid Wander snapshot manifest {manifest_path}: {error}"
        ) from error


def verify_snapshot(snapshot_path: str | Path) -> SnapshotManifest:
    snapshot = Path(snapshot_path).resolve()
    manifest_path = _snapshot_manifest_path(snapshot)
    if not snapshot.is_file():
        raise PersistenceError(f"Wander snapshot does not exist: {snapshot}")
    if not manifest_path.is_file():
        raise PersistenceError(f"Wander snapshot manifest does not exist: {manifest_path}")
    manifest = _load_snapshot_manifest(manifest_path)
    if manifest.snapshot_file != snapshot.name:
        raise PersistenceError(
            f"Wander snapshot manifest names {manifest.snapshot_file}, not {snapshot.name}"
        )
    if snapshot.stat().st_size != manifest.size_bytes:
        raise PersistenceError(f"Wander snapshot size does not match its manifest: {snapshot}")
    if _sha256(snapshot) != manifest.sha256:
        raise PersistenceError(f"Wander snapshot hash does not match its manifest: {snapshot}")
    with closing(_open_read_only(snapshot)) as connection:
        _assert_integrity(connection, str(snapshot))
        if _schema_version(connection) != manifest.schema_version:
            raise PersistenceError(
                f"Wander snapshot schema does not match its manifest: {snapshot}"
            )
        if _ledger_id(connection) != manifest.ledger_id:
            raise PersistenceError(
                f"Wander snapshot ledger identity does not match its manifest: {snapshot}"
            )
        if database_inventory(connection) != manifest.inventory:
            raise PersistenceError(
                f"Wander snapshot inventory does not match its manifest: {snapshot}"
            )
    return manifest


def _latest_verified_snapshot(
    snapshots_directory: Path,
    ledger_id: str,
) -> SnapshotManifest | None:
    manifests = sorted(
        snapshots_directory.glob(f"{SNAPSHOT_PREFIX}*.json"),
        reverse=True,
    )
    for manifest_path in manifests:
        snapshot_path = manifest_path.with_suffix(".db")
        try:
            manifest = verify_snapshot(snapshot_path)
        except PersistenceError:
            continue
        if manifest.ledger_id == ledger_id:
            return manifest
    return None


def prune_snapshots(snapshots_directory: str | Path, retention: int) -> list[Path]:
    if retention < 1:
        raise ValueError("Snapshot retention must be at least one")
    snapshots = Path(snapshots_directory).resolve()
    manifests = sorted(
        snapshots.glob(f"{SNAPSHOT_PREFIX}*.json"),
        reverse=True,
    )
    removed: list[Path] = []
    for manifest_path in manifests[retention:]:
        snapshot_path = manifest_path.with_suffix(".db")
        manifest_path.unlink(missing_ok=True)
        _remove_sqlite_files(snapshot_path)
        removed.append(snapshot_path)
    return removed


def create_verified_snapshot(
    database_path: str | Path,
    snapshots_directory: str | Path,
    *,
    retention: int = DEFAULT_SNAPSHOT_RETENTION,
) -> Path | None:
    database = Path(database_path).resolve()
    snapshots = Path(snapshots_directory).resolve()
    if not database.is_file():
        raise PersistenceError(f"Wander database does not exist: {database}")
    with closing(_open_read_only(database)) as connection:
        _assert_integrity(connection, str(database))
        ledger_id = _ledger_id(connection)
        schema_version = _schema_version(connection)
        inventory = database_inventory(connection)
    latest = _latest_verified_snapshot(snapshots, ledger_id)
    if (
        latest is not None
        and latest.schema_version == schema_version
        and latest.inventory == inventory
    ):
        return None

    snapshots.mkdir(parents=True, exist_ok=True)
    snapshot = snapshots / f"{SNAPSHOT_PREFIX}{_timestamp()}.db"
    manifest_path = _snapshot_manifest_path(snapshot)
    try:
        _backup_database(database, snapshot)
        with closing(_open_read_only(snapshot)) as connection:
            _assert_integrity(connection, str(snapshot))
            snapshot_ledger_id = _ledger_id(connection)
            snapshot_schema = _schema_version(connection)
            snapshot_inventory = database_inventory(connection)
        if snapshot_ledger_id != ledger_id:
            raise PersistenceError("Wander snapshot changed ledger identity during backup")
        if snapshot_schema != schema_version:
            raise PersistenceError("Wander snapshot changed schema during backup")
        manifest = SnapshotManifest(
            snapshot_file=snapshot.name,
            ledger_id=snapshot_ledger_id,
            schema_version=snapshot_schema,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            size_bytes=snapshot.stat().st_size,
            sha256=_sha256(snapshot),
            inventory=snapshot_inventory,
        )
        _write_json_atomic(asdict(manifest), manifest_path)
        verify_snapshot(snapshot)
        prune_snapshots(snapshots, retention)
        return snapshot
    except BaseException:
        manifest_path.unlink(missing_ok=True)
        _remove_sqlite_files(snapshot)
        raise


class SnapshotManager:
    def __init__(
        self,
        database_path: str | Path,
        snapshots_directory: str | Path,
        *,
        retention: int = DEFAULT_SNAPSHOT_RETENTION,
    ):
        if retention < 1:
            raise ValueError("Snapshot retention must be at least one")
        self.database_path = Path(database_path).resolve()
        self.snapshots_directory = Path(snapshots_directory).resolve()
        self.retention = retention

    def create_if_changed(self) -> Path | None:
        return create_verified_snapshot(
            self.database_path,
            self.snapshots_directory,
            retention=self.retention,
        )


def verify_restore_source(
    source_path: str | Path,
    destination_path: str | Path,
    supported_schema_version: int,
) -> RestoreSource:
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    manifest_path = source.with_suffix(".json")
    if not source.is_file():
        raise PersistenceError(f"Wander restore source does not exist: {source}")
    if not manifest_path.is_file():
        raise PersistenceError(
            f"Wander restore source has no verification manifest: {manifest_path}"
        )
    document = _read_json_object(manifest_path)
    if "snapshot_file" in document:
        manifest = verify_snapshot(source)
        restore_source = RestoreSource(
            kind="snapshot",
            path=str(source),
            ledger_id=manifest.ledger_id,
            schema_version=manifest.schema_version,
            sha256=manifest.sha256,
            inventory=manifest.inventory,
        )
    elif document.get("kind") == "restore-safety-backup":
        if document.get("backup_file") != source.name:
            raise PersistenceError("Restore safety manifest names a different backup")
        if Path(str(document.get("database", ""))).resolve() != destination:
            raise PersistenceError("Restore safety backup belongs to a different database")
        restore_source = _verify_legacy_restore_source(
            source,
            document,
            kind="restore-safety-backup",
            expected_schema_field="schema_version",
            expected_hash_field="backup_sha256",
            ledger_id=document.get("ledger_id"),
            expected_destination=str(destination),
        )
    elif "backup_sha256" in document and "source_schema" in document:
        if Path(str(document.get("backup", ""))).resolve() != source:
            raise PersistenceError("Import receipt names a different backup")
        if Path(str(document.get("destination", ""))).resolve() != destination:
            raise PersistenceError("Import backup belongs to a different destination")
        restore_source = _verify_legacy_restore_source(
            source,
            document,
            kind="first-import-backup",
            expected_schema_field="source_schema",
            expected_hash_field="backup_sha256",
            ledger_id=None,
            expected_destination=str(destination),
        )
    else:
        raise PersistenceError(f"Unrecognized Wander restore manifest: {manifest_path}")
    if restore_source.schema_version > supported_schema_version:
        raise PersistenceError(
            f"Restore source schema {restore_source.schema_version} is newer than "
            f"this application supports ({supported_schema_version})"
        )
    return restore_source


def _verify_legacy_restore_source(
    source: Path,
    document: dict[str, Any],
    *,
    kind: str,
    expected_schema_field: str,
    expected_hash_field: str,
    ledger_id: object,
    expected_destination: str,
) -> RestoreSource:
    try:
        expected_schema = int(document[expected_schema_field])
        expected_hash = str(document[expected_hash_field])
        inventory = document["inventory"]
        if not isinstance(inventory, dict):
            raise TypeError("inventory is not an object")
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceError(f"Invalid Wander restore manifest: {error}") from error
    if _sha256(source) != expected_hash:
        raise PersistenceError(f"Wander restore source hash mismatch: {source}")
    with closing(_open_read_only(source)) as connection:
        _assert_integrity(connection, str(source))
        schema_version = _schema_version(connection)
        actual_inventory = database_inventory(connection)
        actual_ledger_id = _optional_ledger_id(connection)
    if schema_version != expected_schema:
        raise PersistenceError(f"Wander restore source schema mismatch: {source}")
    if actual_inventory != inventory:
        raise PersistenceError(f"Wander restore source inventory mismatch: {source}")
    normalized_ledger_id = str(ledger_id) if ledger_id else actual_ledger_id
    if normalized_ledger_id is not None and actual_ledger_id != normalized_ledger_id:
        raise PersistenceError(f"Wander restore source ledger identity mismatch: {source}")
    return RestoreSource(
        kind=kind,
        path=str(source),
        ledger_id=normalized_ledger_id,
        schema_version=schema_version,
        sha256=expected_hash,
        inventory=actual_inventory,
        expected_destination=expected_destination,
    )


def _create_restore_safety_backup(
    database: Path,
    backups_directory: Path,
) -> tuple[Path, RestoreSafetyReceipt]:
    safety_directory = backups_directory / "pre-restore"
    safety_backup = safety_directory / f"pre-restore-{_timestamp()}.db"
    _backup_database(database, safety_backup)
    try:
        with closing(_open_read_only(safety_backup)) as connection:
            _assert_integrity(connection, str(safety_backup))
            receipt = RestoreSafetyReceipt(
                kind="restore-safety-backup",
                backup_file=safety_backup.name,
                database=str(database),
                ledger_id=_optional_ledger_id(connection),
                schema_version=_schema_version(connection),
                backup_sha256=_sha256(safety_backup),
                inventory=database_inventory(connection),
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        _write_json_atomic(asdict(receipt), safety_backup.with_suffix(".json"))
        verify_restore_source(safety_backup, database, receipt.schema_version)
        return safety_backup, receipt
    except BaseException:
        safety_backup.with_suffix(".json").unlink(missing_ok=True)
        _remove_sqlite_files(safety_backup)
        raise


def restore_ledger(
    source_path: str | Path,
    destination_path: str | Path,
    backups_directory: str | Path,
    supported_schema_version: int,
) -> RestoreReceipt:
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    backups = Path(backups_directory).resolve()
    if source == destination:
        raise PersistenceError("Restore source and destination must be different databases")
    restore_directory = backups / "restores"
    pending_path = restore_directory / "restore-pending.json"
    with SingleInstanceLock(destination):
        restore_source = verify_restore_source(
            source,
            destination,
            supported_schema_version,
        )
        pending_matches = False
        if pending_path.is_file():
            pending = _read_json_object(pending_path)
            pending_matches = (
                Path(str(pending.get("source", ""))).resolve() == source
                and Path(str(pending.get("destination", ""))).resolve() == destination
            )
            if not pending_matches:
                raise PersistenceError(
                    "A different Wander restore is incomplete; finish or inspect it first"
                )

        current_ledger_id: str | None = None
        if destination.is_file():
            with closing(_open_read_only(destination)) as connection:
                _assert_integrity(connection, str(destination))
                current_schema = _schema_version(connection)
                if current_schema > supported_schema_version:
                    raise PersistenceError(
                        f"Current ledger schema {current_schema} is newer than this "
                        f"application supports ({supported_schema_version})"
                    )
                current_ledger_id = _optional_ledger_id(connection)
        identity_path = identity_path_for_database(destination)
        registered_ledger_id: str | None = None
        if identity_path.is_file():
            identity = _read_json_object(identity_path)
            if Path(str(identity.get("database", ""))).resolve() != destination:
                raise PersistenceError("Wander ledger identity names a different database path")
            registered_ledger_id = str(identity.get("ledger_id", "")) or None

        if restore_source.ledger_id is not None:
            expected_ids = {
                value for value in (current_ledger_id, registered_ledger_id) if value is not None
            }
            if pending_matches and current_ledger_id == restore_source.ledger_id:
                expected_ids = {restore_source.ledger_id}
            if expected_ids and expected_ids != {restore_source.ledger_id}:
                raise PersistenceError("Restore source belongs to a different Wander ledger")
        elif restore_source.expected_destination != str(destination):
            raise PersistenceError("Legacy restore source is not bound to this destination")

        safety_backup: Path | None = None
        if destination.is_file():
            safety_backup, _ = _create_restore_safety_backup(destination, backups)

        from wander.store import WanderStore

        staged_destination = _temporary_path(destination)
        published = False
        try:
            _backup_database(source, staged_destination)
            staged_store = WanderStore(staged_destination)
            restored_identity = staged_store.ledger_identity()
            restored_ledger_id = str(restored_identity["ledger_id"])
            _finalize_staged_database(staged_destination)
            with closing(_open_read_only(staged_destination)) as connection:
                _assert_integrity(connection, str(staged_destination))
                restored_schema = _schema_version(connection)
                restored_inventory = database_inventory(connection)
            if restored_schema != supported_schema_version:
                raise PersistenceError(
                    f"Restored ledger schema is {restored_schema}; "
                    f"expected {supported_schema_version}"
                )
            if restored_inventory != restore_source.inventory:
                raise PersistenceError("Restored ledger inventory differs from its source")
            if (
                restore_source.ledger_id is not None
                and restored_ledger_id != restore_source.ledger_id
            ):
                raise PersistenceError("Restored ledger identity differs from its source")

            pending_document = {
                "source": str(source),
                "destination": str(destination),
                "restored_ledger_id": restored_ledger_id,
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            _write_json_atomic(pending_document, pending_path)
            if destination.is_file():
                _finalize_staged_database(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_destination, destination)
            published = True
            identity_receipt = LedgerIdentityReceipt(
                database=str(destination),
                ledger_id=restored_ledger_id,
                registered_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            _write_json_atomic(asdict(identity_receipt), identity_path)
            receipt = RestoreReceipt(
                source=str(source),
                source_kind=restore_source.kind,
                destination=str(destination),
                safety_backup=str(safety_backup) if safety_backup is not None else None,
                previous_ledger_id=current_ledger_id or registered_ledger_id,
                restored_ledger_id=restored_ledger_id,
                restored_schema=restored_schema,
                inventory=restored_inventory,
                restored_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            restore_receipt_path = restore_directory / f"restore-{_timestamp()}.json"
            _write_json_atomic(asdict(receipt), restore_receipt_path)
            pending_path.unlink(missing_ok=True)
            return receipt
        except BaseException:
            _remove_sqlite_files(staged_destination)
            if not published:
                pending_path.unlink(missing_ok=True)
            raise


def new_ledger_is_authorized(database_path: str | Path) -> bool:
    database = Path(database_path).resolve()
    authorization_path = new_ledger_authorization_path(database)
    if not authorization_path.is_file():
        return False
    authorization = _read_json_object(authorization_path)
    if Path(str(authorization.get("database", ""))).resolve() != database:
        raise PersistenceError("New-ledger authorization names a different database path")
    return True


def _matching_import_receipts(
    database: Path,
    backups_directory: Path,
) -> list[dict[str, Any]]:
    candidates = [database.parent.parent / "migration" / "first-import.json"]
    if backups_directory.exists():
        candidates.extend(sorted(backups_directory.glob("first-desktop-import-*.json")))
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = _read_json_object(candidate)
            destination = Path(str(payload.get("destination", ""))).resolve()
        except (OSError, PersistenceError):
            continue
        if destination == database:
            matches.append(payload)
    return matches


def _receipt_proves_lineage(
    receipt: dict[str, Any],
    ledger_id: str,
    inventory: dict[str, Any],
) -> bool:
    recorded_ledger_id = receipt.get("ledger_id")
    if recorded_ledger_id is not None:
        return recorded_ledger_id == ledger_id
    recorded_inventory = receipt.get("inventory")
    if not isinstance(recorded_inventory, dict):
        return False
    append_only_tables = (
        "journeys",
        "work_items",
        "thread_references",
        "checkpoints",
        "events",
        "jaunts",
        "jaunt_events",
    )
    for table in append_only_tables:
        previous = recorded_inventory.get(table)
        current = inventory.get(table)
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return False
        if int(current.get("count", -1)) < int(previous.get("count", 0)):
            return False
    return True


def preflight_ledger_identity(
    database_path: str | Path,
    backups_directory: str | Path,
    *,
    allow_unregistered_ledger: bool = False,
) -> None:
    database = Path(database_path).resolve()
    if not database.is_file() or identity_path_for_database(database).is_file():
        return
    if allow_unregistered_ledger or new_ledger_is_authorized(database):
        return
    backups = Path(backups_directory).resolve()
    with closing(_open_read_only(database)) as connection:
        _assert_integrity(connection, str(database))
        inventory = database_inventory(connection)
        ledger_id = ""
        if _table_exists(connection, "ledger_metadata"):
            row = connection.execute(
                "SELECT ledger_id FROM ledger_metadata WHERE singleton = 1"
            ).fetchone()
            if row is not None:
                ledger_id = str(row[0])
    receipts = _matching_import_receipts(database, backups)
    if not any(_receipt_proves_lineage(receipt, ledger_id, inventory) for receipt in receipts):
        raise PersistenceError(
            "Wander refused an unregistered ledger because no verified import receipt "
            "proves its lineage"
        )


def validate_or_register_ledger_identity(
    store: Any,
    database_path: str | Path,
    backups_directory: str | Path,
    *,
    database_existed: bool,
    allow_unregistered_ledger: bool = False,
) -> LedgerIdentityReceipt:
    database = Path(database_path).resolve()
    backups = Path(backups_directory).resolve()
    identity_path = identity_path_for_database(database)
    authorization_path = new_ledger_authorization_path(database)
    identity = store.ledger_identity()
    ledger_id = str(identity["ledger_id"])
    if identity["ever_nonempty"] and not (
        identity["journey_count"] or identity["jaunt_count"]
    ):
        raise PersistenceError(
            "Wander refused an unexpectedly empty ledger that previously contained data"
        )

    if identity_path.is_file():
        document = _read_json_object(identity_path)
        if Path(str(document.get("database", ""))).resolve() != database:
            raise PersistenceError("Wander ledger identity names a different database path")
        if document.get("ledger_id") != ledger_id:
            raise PersistenceError(
                "Wander refused a replacement ledger whose identity does not match this installation"
            )
        authorization_path.unlink(missing_ok=True)
        return LedgerIdentityReceipt(
            database=str(database),
            ledger_id=ledger_id,
            registered_at=str(document.get("registered_at", "")),
        )

    authorized_new = new_ledger_is_authorized(database)

    if database_existed and not authorized_new and not allow_unregistered_ledger:
        with closing(_open_read_only(database)) as connection:
            inventory = database_inventory(connection)
        receipts = _matching_import_receipts(database, backups)
        if not any(
            _receipt_proves_lineage(receipt, ledger_id, inventory) for receipt in receipts
        ):
            raise PersistenceError(
                "Wander refused an unregistered ledger because no verified import receipt "
                "proves its lineage"
            )
    elif not database_existed and not authorized_new and not allow_unregistered_ledger:
        raise PersistenceError(
            "Wander refused to create a replacement ledger without explicit authorization"
        )

    receipt = LedgerIdentityReceipt(
        database=str(database),
        ledger_id=ledger_id,
        registered_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    _write_json_atomic(asdict(receipt), identity_path)
    authorization_path.unlink(missing_ok=True)
    return receipt


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
    if identity_path_for_database(destination).exists():
        raise PersistenceError(
            f"Refusing to overwrite Wander ledger identity: "
            f"{identity_path_for_database(destination)}"
        )

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
            staged_store = WanderStore(staged_destination)
            ledger_id = str(staged_store.ledger_identity()["ledger_id"])
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
                ledger_id=ledger_id,
                migrated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            _write_receipt(receipt, receipt_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(staged_destination, destination)
                identity_receipt = LedgerIdentityReceipt(
                    database=str(destination),
                    ledger_id=ledger_id,
                    registered_at=datetime.now(UTC).isoformat(timespec="seconds"),
                )
                _write_json_atomic(
                    asdict(identity_receipt),
                    identity_path_for_database(destination),
                )
            except BaseException:
                if not destination.exists():
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

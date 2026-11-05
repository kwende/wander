from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WanderPaths:
    root: Path
    database: Path
    backups: Path
    logs: Path

    @classmethod
    def for_current_user(cls, local_app_data: str | Path | None = None) -> WanderPaths:
        configured_root = local_app_data or os.environ.get("LOCALAPPDATA")
        if configured_root is None:
            configured_root = Path.home() / ".local" / "share"
        root = Path(configured_root) / "Wander"
        return cls(
            root=root,
            database=root / "data" / "wander.db",
            backups=root / "backups",
            logs=root / "logs",
        )


def default_paths() -> WanderPaths:
    return WanderPaths.for_current_user()


def default_database_path() -> Path:
    return default_paths().database


def backup_directory_for_database(database_path: str | Path) -> Path:
    database = Path(database_path)
    application_paths = default_paths()
    if database.resolve() == application_paths.database.resolve():
        return application_paths.backups
    return database.parent / "backups"

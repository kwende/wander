from __future__ import annotations

import os
from pathlib import Path


def default_database_path() -> Path:
    configured = os.environ.get("WANDER_DB")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "wander.db"

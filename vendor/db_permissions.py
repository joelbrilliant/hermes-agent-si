"""Private-file permissions for local SQLite memory stores."""

from __future__ import annotations

import os
from pathlib import Path


def enforce_private_database(path: Path | str) -> None:
    """Set the database and any live WAL sidecars to owner-only access."""
    path = Path(path)
    for candidate in (
        path,
        Path(str(path) + "-shm"),
        Path(str(path) + "-wal"),
    ):
        if candidate.exists():
            candidate.chmod(0o600)


def prepare_private_database(path: Path | str) -> Path:
    """Create a database path without a world-readable creation window."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    enforce_private_database(path)
    return path

"""Path helpers for the regulatory research pipeline."""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR_ENV = "REGULATORY_DATA_DIR"


def regulatory_data_dir() -> Path:
    configured = os.getenv(DATA_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return BASE_DIR / "data" / "regulatory"


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(path.resolve())

"""Core normalization, yield-spread calculation, and SQLite schema defaults."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "usdjpy_signals.db"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_SCHEMA_PATH",
    "PROJECT_ROOT",
]

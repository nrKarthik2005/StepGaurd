"""SQLite connection helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from backend import config

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: Optional[Path] = None) -> Path:
    p = Path(path or config.DB_PATH)
    with connect(p) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return p

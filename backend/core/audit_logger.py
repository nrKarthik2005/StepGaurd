"""Application logging and JSONL execution traces.

Only workflow data is written: no environment variables, API keys or tokens are logged.
"""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterable

from backend.config import LOG_PATH

_CONFIGURED = False
SECRET_KEYS = {"token", "api_key", "authorization", "password", "secret"}


def get_logger(name: str = "stepguard") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(name if name.startswith("stepguard") else f"stepguard.{name}")
    if not _CONFIGURED:
        root = logging.getLogger("stepguard")
        root.setLevel(logging.INFO)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
        root.propagate = False
        _CONFIGURED = True
    return logger


def redact(obj: Any) -> Any:
    """Recursively replace values of secret-looking keys."""
    if isinstance(obj, dict):
        return {k: ("***" if str(k).lower() in SECRET_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class TraceWriter:
    """Append-only JSONL writer for raw execution traces."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def write(self, record: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(redact(record), default=str) + "\n")

    def write_many(self, records: Iterable[Dict[str, Any]]) -> None:
        for r in records:
            self.write(r)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def tail_log(lines: int = 200) -> list:
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH, encoding="utf-8", errors="replace") as fh:
        return fh.readlines()[-lines:]

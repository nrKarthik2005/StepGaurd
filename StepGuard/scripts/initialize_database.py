"""Create (or upgrade) the SQLite database and register the workflow specifications.

Usage:  python scripts/initialize_database.py [--reset]
"""
import argparse

import _bootstrap  # noqa: F401

from backend import config
from backend.core.workflow_engine import load_all
from backend.database.connection import init_db
from backend.database.repository import Repository


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="delete the existing database first")
    a = ap.parse_args()
    config.ensure_dirs()
    if a.reset and config.DB_PATH.exists():
        config.DB_PATH.unlink()
        print(f"deleted {config.DB_PATH}")
    init_db()
    wfs = load_all(refresh=True)
    Repository().save_workflows(wfs)
    print(f"database ready: {config.DB_PATH}")
    for name, wf in sorted(wfs.items()):
        print(f"  {name:22s} v{wf.spec.version}  {len(wf.spec.states)} states  "
              f"{len(wf.spec.tools)} tools  sha256 {wf.sha256[:12]}")


if __name__ == "__main__":
    main()

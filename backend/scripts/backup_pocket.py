"""Consistent SQLite backup; reads only the independent Pocket database."""

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    source = (args.data_dir / "pocket.db").resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = args.backup_dir / f"pocket-{stamp}.db"
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as live:
            with sqlite3.connect(destination) as copy:
                live.backup(copy)
                if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Pocket backup did not pass integrity verification")
        destination.chmod(0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    for old in sorted(args.backup_dir.glob("pocket-*.db"), reverse=True)[14:]:
        old.unlink()
    print("Pocket backup verified; the latest 14 snapshots are retained.")


if __name__ == "__main__":
    main()

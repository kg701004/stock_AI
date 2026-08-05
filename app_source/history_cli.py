"""Command-line tools for compact historical-data archive/import/verification."""

from __future__ import annotations

import sys
from pathlib import Path

from historical_storage import archive_and_import, backup_database, verify_archive
from storage_paths import storage_paths


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"import", "verify", "backup"}:
        raise SystemExit("Usage: python history_cli.py import <csv> | verify | backup <destination.sqlite>")
    command = sys.argv[1]
    paths = storage_paths()
    database = paths["history_database"]
    if command == "import":
        if len(sys.argv) != 3:
            raise SystemExit("Usage: python history_cli.py import <daily-bars.csv>")
        checksum, inserted = archive_and_import(Path(sys.argv[2]), database, paths["raw_archive"])
        print(f"checksum={checksum}; imported_rows={inserted}")
    elif command == "verify":
        errors = verify_archive(database)
        print("archive verification passed" if not errors else "\n".join(errors))
        if errors:
            raise SystemExit(1)
    else:
        if len(sys.argv) != 3:
            raise SystemExit("Usage: python history_cli.py backup <destination.sqlite>")
        backup_database(database, Path(sys.argv[2]))
        print("backup completed")


if __name__ == "__main__":
    main()

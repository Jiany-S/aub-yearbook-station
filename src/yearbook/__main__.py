"""Small CLI for verifying the roster foundation before the desktop UI exists."""

import argparse
import json
import sqlite3
import sys

from .roster import RosterValidationError, load_roster
from .store import RosterStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AUB Yearbook tools (live camera integration pending)")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("profile", "import-roster"):
        sub = commands.add_parser(command)
        sub.add_argument("workbook")
        sub.add_argument("--sheet")
        if command == "import-roster":
            sub.add_argument("--database", required=True)
    lookup = commands.add_parser("lookup")
    lookup.add_argument("identifier", help="AUB ID, username, or full imported email")
    lookup.add_argument("--database", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("backup")
    restore.add_argument("destination")
    combine = commands.add_parser("combine")
    combine.add_argument("backups", nargs="+")
    combine.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            from .archives import restore_backup
            result = {"restored_to": str(restore_backup(args.backup, args.destination))}
        elif args.command == "combine":
            from .archives import consolidate_backups
            result = consolidate_backups(args.backups, args.destination)
        elif args.command == "lookup":
            with RosterStore(args.database, read_only=True) as store:
                result = store.find_student(args.identifier)
            if result is None:
                print(json.dumps({"found": False}))
                return 1
        else:
            roster = load_roster(args.workbook, sheet=args.sheet)
            result = roster.summary()
            if args.command == "import-roster":
                with RosterStore(args.database) as store:
                    result["import"] = store.import_roster(roster)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (RosterValidationError, OSError, sqlite3.Error, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

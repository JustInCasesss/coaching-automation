"""Command-line entry point."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from . import db
from .reminders import run_reminders
from .server import serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Coaching Institute Automation")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="Run the HTTP service")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=None)

    sub.add_parser("init-db", help="Create or upgrade the SQLite database")
    sub.add_parser("stats", help="Print aggregate database statistics")

    reminders_parser = sub.add_parser("reminders", help="Preview or run one fee-reminder tick")
    reminders_parser.add_argument("--live", action="store_true", help="Send approved templates where configured")
    reminders_parser.add_argument("--force", action="store_true", help="Override the 09:00-20:00 quiet hours")

    args = parser.parse_args()
    if args.command == "serve":
        serve(args.host, args.port)
        return 0
    if args.command == "init-db":
        db.init_db()
        print("Database ready.")
        return 0
    if args.command == "stats":
        db.init_db()
        print(json.dumps(db.stats(), indent=2))
        return 0
    if args.command == "reminders":
        db.init_db()
        result = run_reminders(dry_run=not args.live, force=args.force)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") and result.get("failed", 0) == 0 else 1
    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

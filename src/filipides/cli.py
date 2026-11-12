"""CLI: python -m filipides {login,sync,wellness,probe,migrate,genkey}"""
import argparse
import logging
import sys
from pathlib import Path

from . import db, garmin
from .config import env
from .crypto import generate_key

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def cmd_login(_args) -> int:
    client = garmin.login_interactive(env("GARMIN_EMAIL", required=True),
                                      env("GARMIN_PASSWORD", required=True))
    with db.connect() as conn:
        user_id = db.resolve_user_id(conn)
        db.save_garmin_token(conn, user_id, garmin.dump_token(client))
        conn.commit()
    print("Garmin session saved (encrypted) to the database.")
    return 0


def cmd_sync(args) -> int:
    from .sync import sync_activities, sync_wellness

    print("activities:", sync_activities(days=args.days, with_splits=not args.no_splits))
    if not args.no_wellness:
        print("wellness:  ", sync_wellness(days=args.wellness_days))
    return 0


def cmd_wellness(args) -> int:
    from .sync import sync_wellness

    print(sync_wellness(days=args.days))
    return 0


def cmd_probe(args) -> int:
    """Diagnostics: what Garmin returns for a given day, showing structure only."""
    import json
    from datetime import date, timedelta

    from .sync import _client_for

    with db.connect() as conn:
        client = _client_for(conn, db.resolve_user_id(conn))
    day = date.today() - timedelta(days=args.back)
    print(f"Probed day: {day.isoformat()}")
    print(json.dumps(garmin.probe_wellness(client, day), indent=2, ensure_ascii=False))
    return 0


def cmd_migrate(args) -> int:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if args.only:
        files = [f for f in files if args.only in f.name]
    if not files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR} (install with `pip install -e .`)")
    with db.connect() as conn:
        for f in files:
            print("applying", f.name)
            conn.execute(f.read_text(encoding="utf-8"))
            conn.commit()
    return 0


def cmd_genkey(_args) -> int:
    print(generate_key())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="filipides")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="First Garmin login (MFA) and store the session").set_defaults(fn=cmd_login)

    p = sub.add_parser("sync", help="Activities + wellness")
    p.add_argument("--days", type=int, default=None,
                   help="History of N days. Without it: incremental since the last activity")
    p.add_argument("--wellness-days", type=int, default=7)
    p.add_argument("--no-splits", action="store_true")
    p.add_argument("--no-wellness", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("wellness", help="Daily wellness only")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_wellness)

    p = sub.add_parser("probe", help="Wellness diagnostics (structure only, no values)")
    p.add_argument("--back", type=int, default=1, help="Days back (1 = yesterday)")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("migrate", help="Apply migrations/*.sql")
    p.add_argument("--only", help="Only files whose name contains this text")
    p.set_defaults(fn=cmd_migrate)

    sub.add_parser("genkey", help="Generate an ENCRYPTION_KEY").set_defaults(fn=cmd_genkey)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        return args.fn(args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Whatever was already saved is kept; you can rerun the command.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

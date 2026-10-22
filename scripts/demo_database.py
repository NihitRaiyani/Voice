#!/usr/bin/env python3
"""Explicit TEST ONLY database lifecycle; never reads the configured working URL."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roma.repositories.postgres.demo_database import start_demo, stop_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('up', 'down'))
    args = parser.parse_args()
    try:
        if args.action == 'up':
            url = start_demo(app_env=os.environ.get('APP_ENV', 'dev'))
            print('TEST ONLY disposable booking data; no working DATABASE_URL used.')
            print('DATABASE_URL=' + url)
        else:
            stop_demo(app_env=os.environ.get('APP_ENV', 'dev'))
            print('TEST ONLY demo database removed.')
    except Exception:  # noqa: BLE001 -- fixed diagnostics, no working URL or private path.
        print('Demo database operation failed: require dev/test, native PostgreSQL and an unused owned demo directory. No working database is changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

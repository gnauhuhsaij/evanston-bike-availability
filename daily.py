"""Recompute one complete Chicago calendar day of prospective model results."""
from __future__ import annotations

import argparse
import datetime as dt
import json

from analytics import LOCAL, write_day


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Chicago date YYYY-MM-DD; defaults to yesterday")
    args = parser.parse_args()
    day = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(LOCAL).date() - dt.timedelta(days=1)
    if day >= dt.datetime.now(LOCAL).date():
        parser.error("only complete previous days can be assessed")
    report = write_day(day)
    print(json.dumps({key: report[key] for key in ("date", "polls", "station_count", "counts", "targets")}, indent=2))


if __name__ == "__main__":
    main()

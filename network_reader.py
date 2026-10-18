"""Read the Chicago-wide station archive written by server.py (data/raw/network/*.jsonl.gz).

    from network_reader import polls
    for stamp, stations in polls("2026-10-19"):   # stations: {station_id: {"bikes": .., ...}}
        ...

`python3 network_reader.py [YYYY-MM-DD]` prints a short summary of one day file.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import pathlib
import sys
import zlib

ROOT = pathlib.Path(__file__).resolve().parent
NETWORK_DIR = pathlib.Path(os.environ.get("NETWORK_SNAPSHOTS_DIR", ROOT / "data" / "raw" / "network"))


def lines(path: pathlib.Path):
    """Yield parsed lines; a truncated final gzip member (e.g. power loss mid-write) is skipped."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except (EOFError, zlib.error, gzip.BadGzipFile):
        return


def polls(day: str, directory: pathlib.Path | None = None):
    path = (directory or NETWORK_DIR) / f"{day}.jsonl.gz"
    meta = {}
    for item in lines(path):
        if item.get("type") == "stations":
            meta[item["fingerprint"]] = [row[0] for row in item["stations"]]
        elif item.get("type") == "poll" and item.get("fingerprint") in meta:
            ids, cols = meta[item["fingerprint"]], item["columns"]
            yield (dt.datetime.fromisoformat(item["t"]),
                   {sid: dict(zip(cols, row)) for sid, row in zip(ids, item["rows"])})


def stations(day: str, directory: pathlib.Path | None = None) -> dict:
    """Latest station metadata (name, lat, lon, capacity, region) seen in a day file."""
    out = {}
    for item in lines((directory or NETWORK_DIR) / f"{day}.jsonl.gz"):
        if item.get("type") == "stations":
            for row in item["stations"]:
                out[row[0]] = dict(zip(item["columns"], row))
    return out


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    count, first, last, size = 0, None, None, 0
    for stamp, snap in polls(day):
        count, first, last, size = count + 1, first or stamp, stamp, len(snap)
    path = NETWORK_DIR / f"{day}.jsonl.gz"
    print(json.dumps({"day": day, "polls": count, "first": str(first), "last": str(last), "stations_last_poll": size,
                      "file_mb": round(path.stat().st_size / 1e6, 2) if path.exists() else 0}, indent=1))

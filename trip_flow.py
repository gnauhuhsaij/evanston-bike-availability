"""Trip-flow model: recent arrival/departure rates for Evanston stations.

The 2022 transition model learns from old station snapshots. This model instead
learns how often bikes leave and arrive at each Evanston station, by weekday/weekend
and hour, from the operator's monthly trip files (2025-10 .. 2026-09 by default),
then turns the current reported stock into a 30-minute probability of zero bikes
or zero open docks with a birth-death chain bounded by the station's capacity.

Usage:
  python3 trip_flow.py count 202609     # count one month of trips (cached, ~1 min)
  python3 trip_flow.py count all        # count every month found in data/raw
  python3 trip_flow.py fit              # build models/trip_flow.json from cached counts

Trip files are read from data/raw/YYYYMM-divvy-tripdata.csv (or .zip).
Known limits: operator rebalancing, dockless e-bike parking and riders turned away
from an empty or full station are not visible in trip records.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import pathlib
import re
import sys
import zipfile
from collections import defaultdict
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
COUNTS = ROOT / "data" / "trip_counts"
STATIONS = ROOT / "data" / "evanston_stations.json"
MODEL = ROOT / "models" / "trip_flow.json"
LOCAL = ZoneInfo("America/Chicago")
PSEUDO_DAYS = 10      # shrink sparse station-hour cells toward the Evanston hourly profile
HORIZON_HOURS = 0.5   # fixed before looking at any prospective outcome; not tuned


def load_stations() -> list[dict]:
    return json.loads(STATIONS.read_text())["stations"]


def trip_file(month: str) -> pathlib.Path | None:
    for suffix in (".csv", ".zip"):
        path = RAW / f"{month}-divvy-tripdata{suffix}"
        if path.exists():
            return path
    return None


def open_rows(path: pathlib.Path):
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        name = next(n for n in archive.namelist() if n.endswith(".csv") and not n.startswith("__MACOSX"))
        return io.TextIOWrapper(archive.open(name), encoding="utf-8", newline="")
    return path.open(newline="", encoding="utf-8")


def parse_local(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value[:19])
    except ValueError:
        return None


def count_month(month: str) -> dict:
    """Departures and arrivals per station, local date and hour, for one month."""
    path = trip_file(month)
    if not path:
        raise SystemExit(f"No trip file for {month} in {RAW}")
    stations = load_stations()
    by_short = {s["short_name"]: s["station_id"] for s in stations}
    by_name = {s["name"]: s["station_id"] for s in stations}
    pattern = re.compile("|".join(re.escape(k) for k in list(by_short) + list(by_name)))
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    totals = {"rows": 0, "evanston_departures": 0, "evanston_arrivals": 0, "bad_time": 0}
    with open_rows(path) as handle:
        header = next(csv.reader([handle.readline()]))
        col = {name: i for i, name in enumerate(header)}
        for line in handle:
            totals["rows"] += 1
            if not pattern.search(line):
                continue
            row = next(csv.reader([line]))
            for kind, idx in (("start", 0), ("end", 1)):
                sid = by_short.get(row[col[f"{kind}_station_id"]]) or by_name.get(row[col[f"{kind}_station_name"]])
                if not sid:
                    continue
                stamp = parse_local(row[col["started_at" if kind == "start" else "ended_at"]])
                if not stamp:
                    totals["bad_time"] += 1
                    continue
                cells[f"{sid}|{stamp.date().isoformat()}|{stamp.hour}"][idx] += 1
                totals["evanston_departures" if idx == 0 else "evanston_arrivals"] += 1
    year, mon = int(month[:4]), int(month[4:])
    first = dt.date(year, mon, 1)
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    result = {"month": month, "source": path.name, "first_day": first.isoformat(), "last_day": last.isoformat(),
              "totals": totals, "cells": dict(cells)}
    COUNTS.mkdir(parents=True, exist_ok=True)
    out = COUNTS / f"{month}.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    tmp.replace(out)
    return result


def daytype(day: dt.date) -> str:
    return "weekend" if day.weekday() >= 5 else "weekday"


def fit() -> dict:
    stations = load_stations()
    months = [json.loads(p.read_text()) for p in COUNTS.glob("*.json")]
    months.sort(key=lambda m: m["month"])
    if not months:
        raise SystemExit("No cached counts; run: python3 trip_flow.py count all")
    # Calendar days covered, and when each station first appears (many opened mid-2026).
    days = []
    for m in months:
        first, last = dt.date.fromisoformat(m["first_day"]), dt.date.fromisoformat(m["last_day"])
        days += [first + dt.timedelta(d) for d in range((last - first).days + 1)]
    opened = {}
    for m in months:
        for key in m["cells"]:
            sid, day, _ = key.split("|")
            opened[sid] = min(opened.get(sid, day), day)
    window_start = days[0].isoformat()
    stable = {sid for sid, day in opened.items() if day <= (days[0] + dt.timedelta(days=7)).isoformat()}
    # Seasonal factor per calendar month from stations open the whole year, so new
    # stations opening do not masquerade as seasonal growth.
    month_volume = {}
    for m in months:
        first, last = dt.date.fromisoformat(m["first_day"]), dt.date.fromisoformat(m["last_day"])
        volume = sum(dep + arr for key, (dep, arr) in m["cells"].items() if key.split("|")[0] in stable)
        month_volume[int(m["month"][4:])] = volume / ((last - first).days + 1)
    mean_volume = sum(month_volume.values()) / len(month_volume)
    factor = {mon: round(v / mean_volume, 4) for mon, v in month_volume.items()}
    n_days = {t: sum(1 for d in days if daytype(d) == t) for t in ("weekday", "weekend")}

    def station_days(sid: str, t: str) -> int:
        start = dt.date.fromisoformat(opened.get(sid, window_start))
        return sum(1 for d in days if d >= start and daytype(d) == t)
    # Deseasonalised counts: each trip is divided by its month's factor.
    adj = defaultdict(float)          # (sid, type, hour, 0|1) -> seasonally adjusted count
    pooled = defaultdict(float)       # (type, hour, 0|1)
    raw_trips = defaultdict(int)      # sid -> departures + arrivals, unadjusted
    for m in months:
        f = factor[int(m["month"][4:])] or 1.0
        for key, (dep, arr) in m["cells"].items():
            sid, day, hour = key.split("|")
            t = daytype(dt.date.fromisoformat(day))
            raw_trips[sid] += dep + arr
            for idx, value in ((0, dep), (1, arr)):
                adj[(sid, t, int(hour), idx)] += value / f
                pooled[(t, int(hour), idx)] += value / f
    model_stations = {}
    for s in stations:
        sid = s["station_id"]
        entry = {"name": s["name"], "short_name": s["short_name"], "trips_seen": 0}
        for idx, label in ((0, "departures_per_hour"), (1, "arrivals_per_hour")):
            entry[label] = {}
            for t in ("weekday", "weekend"):
                own_days = max(1, station_days(sid, t))
                station_total = sum(adj[(sid, t, h, idx)] for h in range(24))
                pooled_total = sum(pooled[(t, h, idx)] for h in range(24)) or 1.0
                rates = []
                for h in range(24):
                    prior = station_total / own_days * pooled[(t, h, idx)] / pooled_total
                    rates.append(round((adj[(sid, t, h, idx)] + PSEUDO_DAYS * prior) / (own_days + PSEUDO_DAYS), 5))
                entry[label][t] = rates
        entry["trips_seen"] = raw_trips[sid]
        entry["first_trip_day"] = opened.get(sid)
        model_stations[sid] = entry
    model = {
        "kind": "trip_flow_birth_death",
        "trained_months": [m["month"] for m in months],
        "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "days": n_days,
        "month_factors": factor,
        "seasonal_factor_stations": len(stable),
        "pseudo_days": PSEUDO_DAYS,
        "horizon_hours": HORIZON_HOURS,
        "stations": model_stations,
    }
    MODEL.parent.mkdir(exist_ok=True)
    tmp = MODEL.with_suffix(".tmp")
    tmp.write_text(json.dumps(model, indent=1) + "\n")
    tmp.replace(MODEL)
    return model


def window_rates(entry: dict, factor: float, when_local: dt.datetime, hours: float) -> tuple[float, float]:
    """Average departure/arrival rate over [when, when + hours], following hour boundaries."""
    dep = arr = 0.0
    cursor, remaining = when_local, hours
    while remaining > 1e-9:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        span = min(remaining, (next_hour - cursor).total_seconds() / 3600)
        t = daytype(cursor.date())
        dep += entry["departures_per_hour"][t][cursor.hour] * span
        arr += entry["arrivals_per_hour"][t][cursor.hour] * span
        cursor, remaining = next_hour, remaining - span
    return dep * factor / hours, arr * factor / hours


def stock_distribution(bikes: int, capacity: int, dep_rate: float, arr_rate: float, hours: float) -> list[float]:
    """Distribution of bikes after `hours` for a birth-death chain on 0..capacity (uniformisation)."""
    p = [0.0] * (capacity + 1)
    p[min(max(bikes, 0), capacity)] = 1.0
    total_rate = dep_rate + arr_rate
    if total_rate <= 0:
        return p
    up, down = arr_rate / total_rate, dep_rate / total_rate
    mean = total_rate * hours
    weight = math.exp(-mean)
    result = [weight * x for x in p]
    accumulated, k = weight, 0
    while accumulated < 1 - 1e-10 and k < 2000:
        nxt = [0.0] * (capacity + 1)
        for i, mass in enumerate(p):
            if not mass:
                continue
            if i < capacity:
                nxt[i + 1] += mass * up
            else:
                nxt[i] += mass * up
            if i > 0:
                nxt[i - 1] += mass * down
            else:
                nxt[i] += mass * down
        p = nxt
        k += 1
        weight *= mean / k
        accumulated += weight
        for i, mass in enumerate(p):
            result[i] += weight * mass
    norm = sum(result)
    return [x / norm for x in result]


def predict(model: dict, station_id: str, bikes: int, docks: int, when_utc: dt.datetime) -> dict | None:
    entry = model["stations"].get(str(station_id))
    if entry is None or bikes is None or docks is None:
        return None
    capacity = int(bikes) + int(docks)
    if capacity <= 0:
        return None
    local = when_utc.astimezone(LOCAL)
    factor = model["month_factors"].get(str(local.month), model["month_factors"].get(local.month, 1.0))
    hours = model.get("horizon_hours", HORIZON_HOURS)
    dep, arr = window_rates(entry, factor, local, hours)
    dist = stock_distribution(int(bikes), capacity, dep, arr, hours)
    return {"no_bike_30m": round(dist[0], 4), "no_dock_30m": round(dist[capacity], 4)}


def load_model() -> dict | None:
    return json.loads(MODEL.read_text()) if MODEL.exists() else None


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"count", "fit"}:
        raise SystemExit(__doc__)
    if sys.argv[1] == "count":
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        months = sorted(p.name[:6] for p in RAW.glob("*-divvy-tripdata.*")) if target == "all" else [target]
        for month in months:
            result = count_month(month)
            print(month, json.dumps(result["totals"]))
    else:
        model = fit()
        print(json.dumps({k: model[k] for k in ("trained_months", "days", "month_factors")}, indent=1))
        for sid, entry in sorted(model["stations"].items(), key=lambda kv: -kv[1]["trips_seen"]):
            print(f'{entry["trips_seen"]:>6}  {entry["name"]}')


if __name__ == "__main__":
    main()

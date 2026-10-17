"""Daily prospective scoring and bounded station history from live snapshots.

Availability changes are observations of reported stock, not trip counts.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import pathlib
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from pilot import ROOT
from train import metrics

UTC = dt.timezone.utc
LOCAL = ZoneInfo("America/Chicago")
SOURCE = pathlib.Path(os.environ.get("SNAPSHOTS_PATH", ROOT / "data/raw/prospective_2026.jsonl"))
DAILY = pathlib.Path(os.environ.get("DAILY_REPORTS_PATH", ROOT / "data/daily"))


def frames(start: dt.datetime, end: dt.datetime):
    if not SOURCE.exists():
        return
    with SOURCE.open() as file:
        for line in file:
            try:
                frame = json.loads(line)
                stamp = dt.datetime.fromisoformat(frame["collected_at"])
            except (ValueError, KeyError, TypeError):
                continue
            if start <= stamp < end:
                yield stamp, frame


def score_day(day: dt.date) -> dict:
    start = dt.datetime.combine(day, dt.time.min, LOCAL).astimezone(UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, LOCAL).astimezone(UTC)
    by_station = defaultdict(list)
    polls = 0
    first_poll = None
    last_poll = None
    for stamp, frame in frames(start, end + dt.timedelta(minutes=35)):
        if stamp < end:
            polls += 1
            first_poll = first_poll or stamp
            last_poll = stamp
        for station in frame.get("stations", []):
            if station.get("station_id") is not None:
                by_station[str(station["station_id"])].append((stamp, station, frame.get("model_version")))
    results = {}
    totals = Counter()
    pooled = {key: {"truth": [], "model": [], "persistence": []} for key in ("no_bike", "no_dock")}
    for station_id, series in by_station.items():
        series.sort(key=lambda row: row[0])
        times = [row[0] for row in series]
        chosen = {}
        for stamp, station, version in series:
            if start <= stamp < end:
                chosen.setdefault(int(stamp.timestamp()) // 600, (stamp, station, version))
        counts = Counter()
        target_values = {key: {"truth": [], "model": [], "persistence": []} for key in pooled}
        for stamp, origin, version in sorted(chosen.values()):
            counts["origin_buckets"] += 1
            age = stamp.timestamp() - (origin.get("last_reported") or 0)
            if not 0 <= age <= 300 or not origin.get("renting") or not origin.get("returning"):
                counts["origin_stale_or_closed"] += 1
                continue
            target = stamp + dt.timedelta(minutes=30)
            position = bisect.bisect_left(times, target)
            candidates = [series[i] for i in (position - 1, position) if 0 <= i < len(series)]
            if not candidates:
                counts["future_missing_or_pending"] += 1
                continue
            future = min(candidates, key=lambda row: abs((row[0] - target).total_seconds()))
            if abs((future[0] - target).total_seconds()) > 180:
                counts["future_missing_or_pending"] += 1
                continue
            actual = future[1]
            actual_age = future[0].timestamp() - (actual.get("last_reported") or 0)
            if not 0 <= actual_age <= 300 or not actual.get("renting") or not actual.get("returning"):
                counts["future_stale_or_closed"] += 1
                continue
            matched = False
            for key, field in (("no_bike", "bikes"), ("no_dock", "docks")):
                if origin.get(field) is None or actual.get(field) is None:
                    counts["missing_count"] += 1
                    continue
                truth = int(actual[field] == 0)
                baseline = float(origin[field] == 0)
                values = target_values[key]
                values["truth"].append(truth)
                values["persistence"].append(baseline)
                risk = (origin.get("experimental_risk") or {}).get(key + "_30m")
                if version and risk is not None:
                    values["model"].append((truth, float(risk), baseline))
                matched = True
            if matched:
                counts["matched"] += 1
        totals.update(counts)
        target_reports = {}
        for key, values in target_values.items():
            pairs = values["model"]
            model = metrics([r[0] for r in pairs], [r[1] for r in pairs]) if pairs else None
            comparison = metrics([r[0] for r in pairs], [r[2] for r in pairs]) if pairs else None
            baseline = metrics(values["truth"], values["persistence"]) if values["truth"] else None
            target_reports[key] = {"model": model, "model_matched_persistence": comparison, "persistence": baseline}
            for truth, prediction, previous in pairs:
                pooled[key]["truth"].append(truth)
                pooled[key]["model"].append(prediction)
                pooled[key]["persistence"].append(previous)
        results[station_id] = {"counts": dict(counts), "targets": target_reports}
    aggregate = {}
    for key, values in pooled.items():
        aggregate[key] = {
            "model": metrics(values["truth"], values["model"]) if values["truth"] else None,
            "persistence": metrics(values["truth"], values["persistence"]) if values["truth"] else None,
        }
    expected = int((end - start).total_seconds() / 60)
    first_ever = None
    if SOURCE.exists():
        with SOURCE.open() as file:
            for line in file:
                try:
                    first_ever = dt.datetime.fromisoformat(json.loads(line)["collected_at"])
                    break
                except (ValueError, KeyError, TypeError):
                    pass
    coverage = {"expected_polls": expected, "observed_polls": polls, "fraction": round(min(1, polls / expected), 4), "first_poll": first_poll.isoformat() if first_poll else None, "last_poll": last_poll.isoformat() if last_poll else None, "initial_partial_day": bool(first_ever and start <= first_ever < end)}
    return {"date": day.isoformat(), "timezone": "America/Chicago", "window_utc": [start.isoformat(), end.isoformat()], "polls": polls, "coverage": coverage, "station_count": len(results), "counts": dict(totals), "targets": aggregate, "stations": results, "metric": "Brier score; lower is better", "label": "reported zero available bikes or docks near 30 minutes"}


def write_day(day: dt.date) -> dict:
    report = score_day(day)
    if not report["polls"]:
        return report
    DAILY.mkdir(parents=True, exist_ok=True)
    target = DAILY / (day.isoformat() + ".json")
    pending = target.with_suffix(".json.tmp")
    pending.write_text(json.dumps(report, indent=2) + "\n")
    pending.replace(target)
    return report


def station_history(station_id: str, window: str, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(UTC)
    days = 1 if window == "day" else 30
    step = 15 * 60 if window == "day" else 60 * 60
    start = now - dt.timedelta(days=days)
    buckets = {}
    for stamp, frame in frames(start, now + dt.timedelta(seconds=1)):
        bucket = int(stamp.timestamp()) // step
        if bucket in buckets:
            continue
        station = next((s for s in frame.get("stations", []) if str(s.get("station_id")) == station_id), None)
        if station:
            age = stamp.timestamp() - (station.get("last_reported") or 0)
            if 0 <= age <= 300 and station.get("renting") and station.get("returning"):
                buckets[bucket] = {"time": stamp.isoformat(), "bikes": station.get("bikes"), "docks": station.get("docks")}
    accuracy = []
    for offset in range(29, -1, -1):
        day = (now.astimezone(LOCAL).date() - dt.timedelta(days=offset + 1))
        path = DAILY / (day.isoformat() + ".json")
        if not path.exists():
            continue
        try:
            daily_report = json.loads(path.read_text())
            record = daily_report["stations"].get(station_id)
        except (ValueError, KeyError):
            continue
        if record:
            accuracy.append({"date": day.isoformat(), "counts": record["counts"], "targets": record["targets"], "coverage": daily_report.get("coverage")})
    availability = []
    if buckets:
        for bucket in range(min(buckets), max(buckets) + 1):
            availability.append(buckets.get(bucket, {"time": dt.datetime.fromtimestamp(bucket * step, UTC).isoformat(), "bikes": None, "docks": None}))
    return {"station_id": station_id, "window": window, "availability": availability, "daily_accuracy": accuracy, "availability_unit": "reported available bikes and open docks; not ride counts", "accuracy_unit": "daily Brier score; lower is better"}

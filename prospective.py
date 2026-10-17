"""Audit prospective 2026 snapshots against outcomes 30 minutes later.

Run while/after the local collector has accumulated data: python3 prospective.py
One origin per station per 10-minute bucket prevents counting near-duplicate
polls as independent examples. No model tuning should use these outcomes.
"""
from __future__ import annotations

import datetime as dt
import bisect
import json
from collections import Counter, defaultdict
from pilot import ROOT
from train import metrics

SOURCE = ROOT / "data" / "raw" / "prospective_2026.jsonl"
OUTPUT = ROOT / "data" / "prospective_metrics.json"


def main() -> None:
    if not SOURCE.exists():
        print("No prospective snapshots yet")
        return
    by_station = defaultdict(list)
    polls = 0
    for line in SOURCE.read_text().splitlines():
        try:
            frame = json.loads(line)
            collected = dt.datetime.fromisoformat(frame["collected_at"])
        except (ValueError, KeyError):
            continue
        polls += 1
        for station in frame.get("stations", []):
            by_station[station["station_id"]].append((collected, station, frame.get("model_version")))
    counts = Counter()
    paired = {"no_bike": ([], []), "no_dock": ([], [])}
    baselines = {"no_bike": [], "no_dock": []}
    by_station_counts = {}
    for station_id, series in by_station.items():
        series.sort(key=lambda r: r[0])
        times = [row[0] for row in series]
        chosen = {}
        for stamp, station, version in series:
            bucket = int(stamp.timestamp()) // 600
            chosen.setdefault(bucket, (stamp, station, version))
        origins = sorted(chosen.values())
        local = Counter()
        for stamp, station, version in origins:
            counts["origin_buckets"] += 1
            local["origin_buckets"] += 1
            risk = station.get("experimental_risk")
            source_age = stamp.timestamp() - (station.get("last_reported") or 0)
            if not risk or not version or not 0 <= source_age <= 300:
                counts["no_valid_prediction"] += 1
                local["no_valid_prediction"] += 1
                continue
            target = stamp + dt.timedelta(minutes=30)
            position = bisect.bisect_left(times, target)
            nearby = [series[i] for i in (position - 1, position) if 0 <= i < len(series)]
            if not nearby:
                counts["future_missing_or_pending"] += 1
                local["future_missing_or_pending"] += 1
                continue
            future = min(nearby, key=lambda row: abs((row[0] - target).total_seconds()))
            gap = abs((future[0] - target).total_seconds())
            if gap > 180:
                counts["future_missing_or_pending"] += 1
                local["future_missing_or_pending"] += 1
                continue
            actual = future[1]
            actual_age = future[0].timestamp() - (actual.get("last_reported") or 0)
            if not 0 <= actual_age <= 300 or not actual.get("renting") or not actual.get("returning"):
                counts["future_stale_or_closed"] += 1
                local["future_stale_or_closed"] += 1
                continue
            for key, count in (("no_bike", "bikes"), ("no_dock", "docks")):
                if actual.get(count) is None or risk.get(key + "_30m") is None:
                    counts["missing_count_or_score"] += 1
                    continue
                truth, prediction = paired[key]
                truth.append(int(actual[count] == 0))
                prediction.append(risk[key + "_30m"])
                baselines[key].append(float(station[count] == 0))
            counts["matched"] += 1
            local["matched"] += 1
        by_station_counts[station_id] = dict(local)
    result = {"polls": polls, "stations": len(by_station), "counts": dict(counts), "targets": {key: {"model": metrics(*values), "persistence": metrics(values[0], baselines[key])} if values[0] else None for key, values in paired.items()}, "by_station_counts": by_station_counts}
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "by_station_counts"}, indent=2))


if __name__ == "__main__":
    main()

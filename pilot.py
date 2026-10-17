"""Small, reproducible label feasibility audit for Evanston Divvy stations.

Run: python3 pilot.py
Uses only the public Socrata and operator-provided GBFS interfaces.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import pathlib
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "pilot_metrics.json"
HIST_URL = "https://data.cityofchicago.org/resource/eq45-8inv.json"
GBFS_BASE = "https://gbfs.lyft.com/gbfs/2.3/chi/en/"
STATIONS = [
    "Benson Ave & Church St",
    "Chicago Ave & Sheridan Rd",
    "Dempster CTA",
    "Central St Metra",
    "Robert Crown Community Center",
    "University Library (NU)",
    "Sheridan Rd & Noyes St (NU)",
    "Dodge Ave & Church St",
]
START = "2022-08-01T00:00:00"
END = "2022-09-08T00:00:00"


def get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "divvy-arrival-risk-feasibility/0.1"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def distance_m(a: dict, b: dict) -> float:
    lat1, lon1 = math.radians(float(a["latitude"])), math.radians(float(a["longitude"]))
    lat2, lon2 = math.radians(float(b["lat"])), math.radians(float(b["lon"]))
    x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2)
    y = lat2 - lat1
    return 6371000 * math.hypot(x, y)


def historical_rows() -> list[dict]:
    RAW.mkdir(parents=True, exist_ok=True)
    cache = RAW / "evanston_2022-08-01_09-07.json"
    if cache.exists():
        return json.loads(cache.read_text())
    quoted = ",".join("'" + name.replace("'", "''") + "'" for name in STATIONS)
    where = f"station_name in ({quoted}) AND timestamp >= '{START}' AND timestamp < '{END}'"
    params = {"$where": where, "$order": "timestamp ASC", "$limit": "50000"}
    rows = get_json(HIST_URL + "?" + urllib.parse.urlencode(params))
    if len(rows) >= 50000:
        raise RuntimeError("Socrata result hit limit; narrow pilot window")
    cache.write_text(json.dumps(rows, ensure_ascii=False))
    return rows


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def audit_horizon(rows: list[dict], minutes: int) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["station_name"]].append(row)
    counts = Counter()
    gaps = []
    labels = Counter()
    per_station = {}
    for name, series in grouped.items():
        series.sort(key=lambda r: r["timestamp"])
        times = [parse_time(r["timestamp"]) for r in series]
        local = Counter()
        for i, row in enumerate(series):
            counts["origin_rows"] += 1
            local["origin_rows"] += 1
            if row.get("status") != "In Service":
                counts["origin_out_of_service"] += 1
                local["origin_out_of_service"] += 1
                continue
            try:
                int(row["available_bikes"])
                int(row["available_docks"])
            except (KeyError, TypeError, ValueError):
                counts["origin_missing_counts"] += 1
                local["origin_missing_counts"] += 1
                continue
            counts["eligible_origins"] += 1
            local["eligible_origins"] += 1
            target = times[i] + dt.timedelta(minutes=minutes)
            k = bisect.bisect_left(times, target)
            candidates = [j for j in (k - 1, k) if 0 <= j < len(series)]
            if not candidates:
                counts["missing_future"] += 1
                local["missing_future"] += 1
                continue
            j = min(candidates, key=lambda j: abs((times[j] - target).total_seconds()))
            gap = abs((times[j] - target).total_seconds()) / 60
            if gap > 5 or j <= i:
                counts["missing_future"] += 1
                local["missing_future"] += 1
                continue
            future = series[j]
            if future.get("status") != "In Service":
                counts["future_out_of_service"] += 1
                local["future_out_of_service"] += 1
                continue
            try:
                bikes, docks = int(future["available_bikes"]), int(future["available_docks"])
            except (KeyError, TypeError, ValueError):
                counts["future_missing_counts"] += 1
                local["future_missing_counts"] += 1
                continue
            counts["matched_labels"] += 1
            local["matched_labels"] += 1
            gaps.append(gap)
            labels["no_bike"] += bikes == 0
            labels["no_dock"] += docks == 0
        per_station[name] = dict(local)
    out = dict(counts)
    out["matching_rate_of_eligible"] = round(counts["matched_labels"] / counts["eligible_origins"], 4) if counts["eligible_origins"] else None
    out["no_bike_labels"] = labels["no_bike"]
    out["no_dock_labels"] = labels["no_dock"]
    out["median_target_offset_minutes"] = sorted(gaps)[len(gaps)//2] if gaps else None
    out["by_station"] = per_station
    return out


def main() -> None:
    rows = historical_rows()
    info = get_json(GBFS_BASE + "station_information.json")
    status = get_json(GBFS_BASE + "station_status.json")
    current = {r["name"]: r for r in info["data"]["stations"]}
    current_status = {r["station_id"]: r for r in status["data"]["stations"]}
    historical_by_name = defaultdict(list)
    for row in rows:
        historical_by_name[row["station_name"]].append(row)
    station_match = {}
    for name in STATIONS:
        old = historical_by_name.get(name, [])
        now = current.get(name)
        station_match[name] = {
            "historical_rows": len(old),
            "current_name_match": now is not None,
            "distance_m": round(distance_m(old[0], now), 1) if old and now else None,
            "current_status_present": now["station_id"] in current_status if now else False,
            "historical_id": old[0].get("id") if old else None,
            "current_id": now.get("station_id") if now else None,
        }
    result = {
        "collected_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "historical_window": [START, END],
        "historical_source": HIST_URL,
        "historical_rows": len(rows),
        "historical_stations": len(historical_by_name),
        "current_info_last_updated": info["last_updated"],
        "current_status_last_updated": status["last_updated"],
        "current_station_count": len(current),
        "current_status_count": len(current_status),
        "station_match": station_match,
        "horizons": {str(m): audit_horizon(rows, m) for m in (15, 30)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "station_match" and k != "horizons"}, ensure_ascii=False, indent=2))
    for m, value in result["horizons"].items():
        print(f"{m}min: eligible={value.get('eligible_origins',0)} matched={value.get('matched_labels',0)} rate={value['matching_rate_of_eligible']} no_bike={value['no_bike_labels']} no_dock={value['no_dock_labels']}")


if __name__ == "__main__":
    main()

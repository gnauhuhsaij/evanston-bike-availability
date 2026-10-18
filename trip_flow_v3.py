"""Trip-flow v3: v1 plus a state-duration correction for stations already empty or full.

v1's birth-death chain is memoryless: a station that has been empty for 3 hours gets the
same recovery chance as one that emptied a minute ago. Live snapshots show long-stuck
stations (no rider or truck activity) stay stuck more often than v1 expects.

For an origin where the station is ALREADY empty (no-bike target) or full (no-dock target):
  1. measure how long it has been in that state (minutes, from consecutive usable polls);
  2. look up, pooled over all stations, past origins in the same state and duration bin whose
     30-minute outcome was already known before this origin (online; no future data);
  3. shrink that empirical stay-rate toward v1's own probability:
         p_v3 = (stayed + A * p_v1) / (seen + A)
Origins not already in the event state keep v1's probability unchanged.

Constants (bins, A, gap rule) were fixed before any v3 scoring and are recorded with the
freeze time in models/trip_flow_v3.json. Only origins at or after `frozen_at` count as a test;
the idea came from looking at 16-18 Oct data, so v3 numbers on that period are biased.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import pathlib
from collections import defaultdict

from trip_flow import MODEL as V1_MODEL, predict

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = ROOT / "models" / "trip_flow_v3.json"
CONSTANTS = {
    "duration_bins_min": [0, 15, 60, 180],  # [0,15), [15,60), [60,180), 180+
    "prior_strength": 20,                    # pseudo-origins at v1's probability
    "max_gap_min": 15,                       # a longer gap in usable polls restarts the duration clock
    "fresh_s": 300,
    "horizon_min": 30,
    "match_tolerance_s": 180,
    "origin_bucket_s": 600,                  # same 10-minute origin thinning as the evaluation
}
TARGETS = (("no_bike", "bikes"), ("no_dock", "docks"))


def write_config(frozen_at: str | None = None) -> dict:
    existing = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    config = {"kind": "trip_flow_v3_state_duration", "base_model": V1_MODEL.name, "constants": CONSTANTS,
              "frozen_at": existing.get("frozen_at") or frozen_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "note": "Only origins at or after frozen_at are an out-of-sample test of v3."}
    CONFIG.parent.mkdir(exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=1) + "\n")
    tmp.replace(CONFIG)
    return config


def load_config() -> dict | None:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else None


def duration_bin(minutes: float) -> int:
    return bisect.bisect_right(CONSTANTS["duration_bins_min"], minutes) - 1


def usable(station: dict, stamp: dt.datetime) -> bool:
    age = stamp.timestamp() - (station.get("last_reported") or 0)
    return (0 <= age <= CONSTANTS["fresh_s"] and bool(station.get("renting")) and bool(station.get("returning"))
            and station.get("bikes") is not None and station.get("docks") is not None)


class DurationIndex:
    """For every station: how long it has been empty/full at any poll, and a pooled table of
    past outcomes keyed by the time each outcome became known."""

    def __init__(self, by_station: dict[str, list[tuple[dt.datetime, dict]]]):
        c = CONSTANTS
        self.runs: dict[str, tuple[list[dt.datetime], list[tuple[float, float]]]] = {}
        # outcome events: (known_at, target, bin, stayed)
        events = defaultdict(list)
        for sid, series in by_station.items():
            series = sorted(((t, s) for t, s in series if usable(s, t)), key=lambda r: r[0])
            times = [t for t, _ in series]
            durations = []
            start = {"bikes": None, "docks": None}
            previous = None
            for t, s in series:
                gap_break = previous is not None and (t - previous).total_seconds() > c["max_gap_min"] * 60
                row = []
                for _, field in TARGETS:
                    if s[field] == 0:
                        if start[field] is None or gap_break:
                            start[field] = t
                        row.append((t - start[field]).total_seconds() / 60)
                    else:
                        start[field] = None
                        row.append(-1.0)
                durations.append(tuple(row))
                previous = t
            self.runs[str(sid)] = (times, durations)
            # Past origins (thinned to one per 10 minutes) and their outcomes.
            chosen = {}
            for i, (t, _) in enumerate(series):
                chosen.setdefault(int(t.timestamp()) // c["origin_bucket_s"], i)
            for i in sorted(chosen.values()):
                t, s = series[i]
                target_time = t + dt.timedelta(minutes=c["horizon_min"])
                j = bisect.bisect_left(times, target_time)
                near = [k for k in (j - 1, j) if 0 <= k < len(times)]
                if not near:
                    continue
                k = min(near, key=lambda k: abs((times[k] - target_time).total_seconds()))
                if abs((times[k] - target_time).total_seconds()) > c["match_tolerance_s"]:
                    continue
                for idx, (key, field) in enumerate(TARGETS):
                    minutes = durations[i][idx]
                    if minutes < 0:
                        continue
                    stayed = int(series[k][1][field] == 0)
                    events[(key, duration_bin(minutes))].append((times[k], stayed))
        self.known = {}
        for cell, items in events.items():
            items.sort(key=lambda e: e[0])
            stamps, cumulative = [e[0] for e in items], [0]
            for _, stayed in items:
                cumulative.append(cumulative[-1] + stayed)
            self.known[cell] = (stamps, cumulative)

    def state_minutes(self, station_id: str, when: dt.datetime) -> tuple[float, float] | None:
        """Minutes empty / full as of the last usable poll at or before `when` (-1 = not in state)."""
        times, durations = self.runs.get(str(station_id), ([], []))
        i = bisect.bisect_right(times, when) - 1
        if i < 0 or (when - times[i]).total_seconds() > CONSTANTS["max_gap_min"] * 60:
            return None
        return durations[i]

    def history(self, key: str, bin_index: int, when: dt.datetime) -> tuple[int, int]:
        """(seen, stayed) for outcomes that were already observed strictly before `when`."""
        stamps, cumulative = self.known.get((key, bin_index), ([], [0]))
        n = bisect.bisect_left(stamps, when)
        return n, cumulative[n]


def predict_v3(v1: dict, index: DurationIndex, station_id: str, bikes: int, docks: int, when: dt.datetime) -> dict | None:
    base = predict(v1, station_id, bikes, docks, when)
    if base is None:
        return None
    out = {"no_bike_30m": base["no_bike_30m"], "no_dock_30m": base["no_dock_30m"], "info": {}}
    minutes = index.state_minutes(station_id, when)
    if minutes is None:
        return out
    a = CONSTANTS["prior_strength"]
    for idx, (key, field) in enumerate(TARGETS):
        current = bikes if field == "bikes" else docks
        if current != 0 or minutes[idx] < 0:
            continue
        b = duration_bin(minutes[idx])
        seen, stayed = index.history(key, b, when)
        p1 = base[key + "_30m"]
        out[key + "_30m"] = round((stayed + a * p1) / (seen + a), 4)
        out["info"][key] = {"minutes": round(minutes[idx], 1), "bin": b, "seen": seen, "stayed": stayed}
    return out


if __name__ == "__main__":
    print(json.dumps(write_config(), indent=1))

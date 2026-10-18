"""Trip-flow v2: v1 rates plus three corrections learned online from live snapshots.

v1 (trip_flow.py, models/trip_flow.json) is left unchanged. v2 reuses its hourly
departure/arrival rates and adds, for each prediction, using ONLY snapshots that
were collected before the prediction time:

  1. hidden demand  - trips are only recorded while a bike (or dock) is available, so
                      v1 under-states demand at stations that often run empty or full.
                      Departure rate is divided by the share of earlier polls with bikes > 0
                      (arrivals: with open docks > 0), shrunk toward no correction.
  2. live intensity - bikes leaving/arriving in the last 60 minutes vs what v1 expected,
                      combined as a Gamma-Poisson multiplier (shrunk toward 1).
  3. rebalancing    - jumps of >= jump_min bikes between consecutive polls are treated as
                      operator trucks; their rate (per station, shrunk to the Evanston rate)
                      and typical size enter the 30-minute chain as jump events.

All constants below were fixed before scoring v2; none were tuned on outcomes.
Usage: imported by compare_models.py; `python3 trip_flow_v2.py` writes the config file.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import pathlib

from trip_flow import LOCAL, MODEL as V1_MODEL, window_rates

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = ROOT / "models" / "trip_flow_v2.json"

CONSTANTS = {
    "max_poll_gap_s": 150,          # consecutive polls further apart are not differenced
    "fresh_s": 300,                 # report age that counts as fresh (same as evaluation)
    "jump_min": 4,                  # |change| between two polls attributed to a truck
    "intensity_window_min": 60,
    "intensity_min_coverage": 0.5,  # share of the window that must be observed
    "intensity_prior_trips": 2.0,   # Gamma prior strength, in expected trips
    "intensity_bounds": [0.25, 4.0],
    "censor_prior_polls": 600,      # ~10 hours at full availability
    "censor_max_correction": 2.0,
    "rebalance_prior_hours": 24.0,  # station rate shrinks to the pooled Evanston rate
    "rebalance_default_size": 5,
}
COMPONENTS = ("censor", "intensity", "rebalance")


def write_config() -> dict:
    config = {"kind": "trip_flow_v2_online", "base_model": V1_MODEL.name, "constants": CONSTANTS,
              "components": list(COMPONENTS),
              "note": "Corrections are estimated at prediction time from snapshots before that time."}
    CONFIG.parent.mkdir(exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=1) + "\n")
    tmp.replace(CONFIG)
    return config


def usable(station: dict, stamp: dt.datetime) -> bool:
    age = stamp.timestamp() - (station.get("last_reported") or 0)
    return (0 <= age <= CONSTANTS["fresh_s"] and bool(station.get("renting")) and bool(station.get("returning"))
            and station.get("bikes") is not None and station.get("docks") is not None)


class StationHistory:
    """Prefix sums over one station's polls so 'strictly before time t' queries are O(log n)."""

    def __init__(self, series: list[tuple[dt.datetime, dict]]):
        c = CONSTANTS
        series = sorted(series, key=lambda s: s[0])
        self.times = [s[0] for s in series]
        self.points = [(s[0], s[1].get("bikes"), s[1].get("docks"), usable(s[1], s[0])) for s in series]
        self.cum_polls, self.cum_bike, self.cum_dock = [0], [0], [0]
        for _, b, d, ok in self.points:
            self.cum_polls.append(self.cum_polls[-1] + ok)
            self.cum_bike.append(self.cum_bike[-1] + (ok and b > 0))
            self.cum_dock.append(self.cum_dock[-1] + (ok and d > 0))
        # Differenced intervals between consecutive usable polls, keyed by their end time.
        self.intervals = []  # (end_time, hours, delta, prev_bikes, prev_docks, is_jump)
        for (t0, b0, d0, ok0), (t1, b1, d1, ok1) in zip(self.points, self.points[1:]):
            gap = (t1 - t0).total_seconds()
            if ok0 and ok1 and 0 < gap <= c["max_poll_gap_s"]:
                delta = b1 - b0
                self.intervals.append((t1, gap / 3600, delta, b0, d0, abs(delta) >= c["jump_min"]))
        self.iv_times = [iv[0] for iv in self.intervals]
        self.cum_hours = [0.0]
        self.jump_up, self.jump_down, self.jump_sizes = [], [], []
        for t1, hours, delta, _b, _d, is_jump in self.intervals:
            self.cum_hours.append(self.cum_hours[-1] + hours)
            if is_jump:
                (self.jump_up if delta > 0 else self.jump_down).append(t1)
                self.jump_sizes.append((t1, abs(delta)))

    # Every query uses bisect_left on `t`, so a poll stamped exactly at t is excluded.
    def availability(self, t: dt.datetime) -> tuple[int, int, int]:
        i = bisect.bisect_left(self.times, t)
        return self.cum_polls[i], self.cum_bike[i], self.cum_dock[i]

    def observed_hours(self, t: dt.datetime) -> float:
        return self.cum_hours[bisect.bisect_left(self.iv_times, t)]

    def jumps(self, t: dt.datetime) -> tuple[int, int, list[int]]:
        return (bisect.bisect_left(self.jump_up, t), bisect.bisect_left(self.jump_down, t),
                [size for when, size in self.jump_sizes if when < t])

    def recent(self, t: dt.datetime, minutes: int) -> list[tuple]:
        lo = bisect.bisect_left(self.iv_times, t - dt.timedelta(minutes=minutes))
        hi = bisect.bisect_left(self.iv_times, t)
        return self.intervals[lo:hi]


class Network:
    """All stations' histories, plus pooled rebalancing statistics before time t."""

    def __init__(self, by_station: dict[str, list[tuple[dt.datetime, dict]]]):
        self.stations = {str(sid): StationHistory(series) for sid, series in by_station.items()}
        self._pool_cache: dict[dt.datetime, tuple[float, float, int]] = {}

    def pooled_rebalancing(self, t: dt.datetime) -> tuple[float, float, int]:
        if t in self._pool_cache:
            return self._pool_cache[t]
        hours = up = down = 0.0
        sizes: list[int] = []
        for hist in self.stations.values():
            hours += hist.observed_hours(t)
            u, d, s = hist.jumps(t)
            up, down, sizes = up + u, down + d, sizes + s
        size = round(sum(sizes) / len(sizes)) if sizes else CONSTANTS["rebalance_default_size"]
        result = ((up / hours) if hours else 0.0, (down / hours) if hours else 0.0, max(1, size))
        self._pool_cache[t] = result
        return result


def stock_distribution_jumps(bikes: int, capacity: int, dep: float, arr: float,
                             up_rate: float, down_rate: float, jump: int, hours: float) -> list[float]:
    """Birth-death chain on 0..capacity plus truck jumps of +/- `jump` bikes (uniformisation)."""
    p = [0.0] * (capacity + 1)
    p[min(max(bikes, 0), capacity)] = 1.0
    total = dep + arr + up_rate + down_rate
    if total <= 0:
        return p
    probs = (arr / total, dep / total, up_rate / total, down_rate / total)
    mean = total * hours
    weight = math.exp(-mean)
    result = [weight * x for x in p]
    accumulated, k = weight, 0
    while accumulated < 1 - 1e-10 and k < 4000:
        nxt = [0.0] * (capacity + 1)
        for i, mass in enumerate(p):
            if not mass:
                continue
            nxt[min(i + 1, capacity)] += mass * probs[0]
            nxt[max(i - 1, 0)] += mass * probs[1]
            nxt[min(i + jump, capacity)] += mass * probs[2]
            nxt[max(i - jump, 0)] += mass * probs[3]
        p = nxt
        k += 1
        weight *= mean / k
        accumulated += weight
        for i, mass in enumerate(p):
            result[i] += weight * mass
    norm = sum(result)
    return [x / norm for x in result]


def predict_v2(v1: dict, network: Network, station_id: str, bikes: int, docks: int, when: dt.datetime,
               use: tuple[str, ...] = COMPONENTS) -> dict | None:
    """30-minute risks using only information collected before `when`. `use` selects components."""
    c = CONSTANTS
    entry = v1["stations"].get(str(station_id))
    hist = network.stations.get(str(station_id))
    if entry is None or hist is None or bikes is None or docks is None:
        return None
    capacity = int(bikes) + int(docks)
    if capacity <= 0:
        return None
    local = when.astimezone(LOCAL)
    factor = v1["month_factors"].get(str(local.month), 1.0)
    hours = v1.get("horizon_hours", 0.5)
    dep, arr = window_rates(entry, factor, local, hours)
    info: dict = {}

    if "censor" in use:
        polls, with_bike, with_dock = hist.availability(when)
        prior = c["censor_prior_polls"]
        share_bike = (with_bike + prior) / (polls + prior)
        share_dock = (with_dock + prior) / (polls + prior)
        dep *= min(c["censor_max_correction"], 1 / max(share_bike, 1e-6))
        arr *= min(c["censor_max_correction"], 1 / max(share_dock, 1e-6))
        info["available_share"] = (round(share_bike, 3), round(share_dock, 3))

    if "intensity" in use:
        window = c["intensity_window_min"]
        recent = [iv for iv in hist.recent(when, window) if not iv[5]]
        covered = sum(iv[1] for iv in recent)
        if covered >= c["intensity_min_coverage"] * window / 60:
            past_dep, past_arr = window_rates(entry, factor, local - dt.timedelta(minutes=window), window / 60)
            exp_dep = past_dep * sum(iv[1] for iv in recent if iv[3] > 0)
            exp_arr = past_arr * sum(iv[1] for iv in recent if iv[4] > 0)
            obs_dep = sum(-iv[2] for iv in recent if iv[2] < 0)
            obs_arr = sum(iv[2] for iv in recent if iv[2] > 0)
            a, (lo, hi) = c["intensity_prior_trips"], c["intensity_bounds"]
            m_dep = min(hi, max(lo, (obs_dep + a) / (exp_dep + a)))
            m_arr = min(hi, max(lo, (obs_arr + a) / (exp_arr + a)))
            dep, arr = dep * m_dep, arr * m_arr
            info["intensity"] = (round(m_dep, 3), round(m_arr, 3))

    up_rate = down_rate = 0.0
    jump = c["rebalance_default_size"]
    if "rebalance" in use:
        pool_up, pool_down, jump = network.pooled_rebalancing(when)
        own_hours = hist.observed_hours(when)
        own_up, own_down, _ = hist.jumps(when)
        prior = c["rebalance_prior_hours"]
        up_rate = (own_up + prior * pool_up) / (own_hours + prior)
        down_rate = (own_down + prior * pool_down) / (own_hours + prior)
        info["rebalance"] = (round(up_rate, 4), round(down_rate, 4), jump)

    dist = stock_distribution_jumps(int(bikes), capacity, dep, arr, up_rate, down_rate, jump, hours)
    return {"no_bike_30m": round(dist[0], 4), "no_dock_30m": round(dist[capacity], 4), "info": info}


if __name__ == "__main__":
    print(json.dumps(write_config(), indent=1))

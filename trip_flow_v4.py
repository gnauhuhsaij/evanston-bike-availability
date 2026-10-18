"""Trip-flow v4: v1 rates multiplied by a weather factor learned from the 2025-26 trips.

    rate_v4 = rate_v1 * exp(beta . x(weather in that hour))

beta comes from a Poisson regression on hourly Evanston trip totals (departures + arrivals,
2025-10 .. 2026-09) with v1's own expected count as the offset, so it only explains what v1
does not: unusually cold/hot hours, rain, snow and wind relative to v1's month factor.
Features are fixed in weather.py. v1 (models/trip_flow.json) is not changed.

Usage:
  python3 trip_flow_v4.py fit    # -> models/trip_flow_v4.json (prints a Jul-Sep holdout check)
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import sys
from collections import defaultdict

from trip_flow import COUNTS, LOCAL, MODEL as V1_MODEL, daytype, load_model, stock_distribution
import weather

ROOT = pathlib.Path(__file__).resolve().parent
MODEL = ROOT / "models" / "trip_flow_v4.json"
HOLDOUT_FROM = "202607"   # used only for the printed sanity check; the saved model uses all months


def hourly_table(v1: dict) -> list[dict]:
    """One row per local hour: observed Evanston trips, v1-expected trips, month."""
    observed = defaultdict(int)
    months = []
    for path in sorted(COUNTS.glob("*.json")):
        m = json.loads(path.read_text())
        months.append(m)
        for key, (dep, arr) in m["cells"].items():
            _sid, day, hour = key.split("|")
            observed[(day, int(hour))] += dep + arr
    rows = []
    stations = v1["stations"]
    for m in months:
        first, last = dt.date.fromisoformat(m["first_day"]), dt.date.fromisoformat(m["last_day"])
        factor = v1["month_factors"][str(int(m["month"][4:]))]
        day = first
        while day <= last:
            t = daytype(day)
            open_stations = [e for e in stations.values() if (e.get("first_trip_day") or "0") <= day.isoformat()]
            for hour in range(24):
                expected = factor * sum(e["departures_per_hour"][t][hour] + e["arrivals_per_hour"][t][hour] for e in open_stations)
                rows.append({"month": m["month"], "day": day, "hour": hour, "y": observed.get((day.isoformat(), hour), 0),
                             "expected": expected})
            day += dt.timedelta(days=1)
    return rows


def attach_features(rows: list[dict], table: dict) -> list[dict]:
    out = []
    for r in rows:
        x = weather.features(table, dt.datetime.combine(r["day"], dt.time(r["hour"])))
        if x is not None and r["expected"] > 0:
            out.append({**r, "x": x})
    return out


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        if abs(m[col][col]) < 1e-12:
            m[col][col] = 1e-12
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_poisson(rows: list[dict], ridge: float = 1.0, iterations: int = 50) -> tuple[list[float], list[float]]:
    """IRLS for log(mu) = log(expected) + x.beta, light ridge on non-intercept terms. Returns (beta, se)."""
    k = len(rows[0]["x"])
    beta = [0.0] * k
    for _ in range(iterations):
        xtwx = [[0.0] * k for _ in range(k)]
        xtwz = [0.0] * k
        for r in rows:
            eta = sum(b * v for b, v in zip(beta, r["x"]))
            mu = r["expected"] * math.exp(eta)
            z = eta + (r["y"] - mu) / mu
            for i in range(k):
                wi = mu * r["x"][i]
                xtwz[i] += wi * z
                for j in range(k):
                    xtwx[i][j] += wi * r["x"][j]
        for i in range(1, k):
            xtwx[i][i] += ridge
        new = solve(xtwx, xtwz)
        done = max(abs(a - b) for a, b in zip(new, beta)) < 1e-8
        beta = new
        if done:
            break
    # Standard errors from the inverse information matrix (diagonal via k solves).
    se = []
    for i in range(k):
        unit = [0.0] * k
        unit[i] = 1.0
        se.append(math.sqrt(max(solve(xtwx, unit)[i], 0.0)))
    return beta, se


def deviance(rows: list[dict], beta: list[float]) -> float:
    total = 0.0
    for r in rows:
        mu = r["expected"] * math.exp(sum(b * v for b, v in zip(beta, r["x"])))
        y = r["y"]
        total += 2 * ((y * math.log(y / mu) if y > 0 else 0.0) - (y - mu))
    return total


def fit() -> dict:
    v1 = load_model()
    rows = attach_features(hourly_table(v1), weather.load())
    train = [r for r in rows if r["month"] < HOLDOUT_FROM]
    hold = [r for r in rows if r["month"] >= HOLDOUT_FROM]
    k = len(weather.FEATURES)
    beta_train, _ = fit_poisson(train)
    intercept_only, _ = fit_poisson([{**r, "x": [1.0]} for r in train])
    check = {
        "train_months": f"{train[0]['month']}..{train[-1]['month']}", "holdout_months": f"{hold[0]['month']}..{hold[-1]['month']}",
        "holdout_deviance_v1": round(deviance(hold, [0.0] * k), 1),
        "holdout_deviance_v1_recalibrated": round(deviance([{**r, "x": [1.0]} for r in hold], intercept_only), 1),
        "holdout_deviance_weather": round(deviance(hold, beta_train), 1),
        "holdout_hours": len(hold),
    }
    beta, se = fit_poisson(rows)
    model = {
        "kind": "trip_flow_v4_weather",
        "base_model": V1_MODEL.name,
        "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "hours_used": len(rows),
        "features": list(weather.FEATURES),
        "beta": [round(b, 5) for b in beta],
        "std_error": [round(s, 5) for s in se],
        "multiplier": {f: round(math.exp(b), 3) for f, b in zip(weather.FEATURES, beta)},
        "holdout_check": check,
        "weather_source": "Open-Meteo archive, hourly, nearest grid point to 42.045N -87.688E",
    }
    MODEL.parent.mkdir(exist_ok=True)
    tmp = MODEL.with_suffix(".tmp")
    tmp.write_text(json.dumps(model, indent=1) + "\n")
    tmp.replace(MODEL)
    return model


def load_v4() -> dict | None:
    return json.loads(MODEL.read_text()) if MODEL.exists() else None


def predict_v4(v1: dict, v4: dict, table: dict, station_id: str, bikes: int, docks: int, when: dt.datetime) -> dict | None:
    """v1's chain with both rates scaled by the weather multiplier for the hour of `when`."""
    from trip_flow import window_rates
    entry = v1["stations"].get(str(station_id))
    if entry is None or bikes is None or docks is None:
        return None
    capacity = int(bikes) + int(docks)
    if capacity <= 0:
        return None
    local = when.astimezone(LOCAL)
    factor = v1["month_factors"].get(str(local.month), 1.0)
    hours = v1.get("horizon_hours", 0.5)
    dep, arr = window_rates(entry, factor, local, hours)
    x = weather.features(table, local.replace(tzinfo=None))
    multiplier = math.exp(sum(b * v for b, v in zip(v4["beta"], x))) if x is not None else 1.0
    dist = stock_distribution(int(bikes), capacity, dep * multiplier, arr * multiplier, hours)
    return {"no_bike_30m": round(dist[0], 4), "no_dock_30m": round(dist[capacity], 4),
            "weather_multiplier": round(multiplier, 3), "weather_missing": x is None}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "fit":
        raise SystemExit(__doc__)
    result = fit()
    print(json.dumps({k: result[k] for k in ("hours_used", "multiplier", "std_error", "holdout_check")}, indent=1))

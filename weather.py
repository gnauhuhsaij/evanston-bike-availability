"""Hourly Evanston weather (Open-Meteo) and the weather features used by trip_flow_v4.

Data file: data/weather/evanston_hourly.csv, local (America/Chicago) wall-clock hours.
Source: https://open-meteo.com (archive API for history; the forecast API serves the
current/next hours when running live). Grid point nearest 42.045 N, -87.688 E.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
WEATHER = ROOT / "data" / "weather" / "evanston_hourly.csv"
FIELDS = ("temperature_c", "precipitation_mm", "snowfall_cm", "snow_depth_m", "wind_kmh", "weather_code")
FEATURES = ("intercept", "cold_below_10c", "hot_above_27c", "rain_now", "heavy_rain_now",
            "rain_previous_2h", "snowing", "snow_on_ground", "wind_above_20kmh")
FORECAST_URL = ("https://api.open-meteo.com/v1/forecast?latitude=42.0451&longitude=-87.6877"
                "&hourly=temperature_2m,precipitation,snowfall,snow_depth,wind_speed_10m,weather_code"
                "&timezone=America%2FChicago&wind_speed_unit=kmh&past_days=2&forecast_days=2")


def hour_key(local: dt.datetime) -> str:
    return local.strftime("%Y-%m-%dT%H:00")


def load(path: pathlib.Path = WEATHER) -> dict[str, dict]:
    table = {}
    if not path.exists():
        return table
    with path.open() as handle:
        for row in csv.DictReader(handle):
            table[row["local_hour"]] = {k: float(row[k]) for k in FIELDS}
    return table


def merge_forecast(table: dict[str, dict], payload: dict) -> int:
    """Add/overwrite hours from an Open-Meteo forecast response (live use)."""
    h = payload["hourly"]
    for i, stamp in enumerate(h["time"]):
        values = (h["temperature_2m"][i], h["precipitation"][i], h["snowfall"][i], h["snow_depth"][i],
                  h["wind_speed_10m"][i], h["weather_code"][i])
        if None not in values:
            table[stamp[:13] + ":00"] = dict(zip(FIELDS, map(float, values)))
    return len(h["time"])


def fetch_forecast(timeout: float = 15) -> dict:
    req = urllib.request.Request(FORECAST_URL, headers={"User-Agent": "arrival-risk-research-prototype/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def features(table: dict[str, dict], local: dt.datetime) -> list[float] | None:
    """Feature vector for the local hour containing `local`; None if that hour is missing."""
    now = table.get(hour_key(local))
    if now is None:
        return None
    previous = [table.get(hour_key(local - dt.timedelta(hours=k))) for k in (1, 2)]
    t, p, w = now["temperature_c"], now["precipitation_mm"], now["wind_kmh"]
    rain_now = p >= 0.1
    rain_before = any(x and x["precipitation_mm"] >= 0.1 for x in previous)
    return [
        1.0,
        max(0.0, 10.0 - t) / 10.0,
        max(0.0, t - 27.0) / 10.0,
        float(rain_now),
        float(p >= 2.5),
        float(rain_before and not rain_now),
        float(now["snowfall_cm"] > 0),
        float(now["snow_depth_m"] >= 0.02),
        max(0.0, w - 20.0) / 10.0,
    ]

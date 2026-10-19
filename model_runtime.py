"""Champion/challenger model runtime for the live server.

models/registry.json names the champion (shown on the site) and the challengers (computed
and logged in every snapshot, never shown). Switching champion = edit the registry and deploy
(or drop a data/registry_override.json on the server). Every model only sees information
available at prediction time: v2/v3 use the server's own recent snapshots, v4 uses weather
fetched from Open-Meteo for the current and previous hours.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import threading
import time
from collections import defaultdict, deque

import trip_flow
import trip_flow_v2
import trip_flow_v3
import trip_flow_v4
import weather
from train import transition_predict

ROOT = pathlib.Path(__file__).resolve().parent
REGISTRY = ROOT / "models" / "registry.json"
OVERRIDE = pathlib.Path(os.environ.get("REGISTRY_OVERRIDE", ROOT / "data" / "registry_override.json"))
HISTORY_DAYS = 7          # snapshots kept in memory for v3's outcome table
V2_HISTORY_HOURS = 48     # v2's network statistics use the most recent two days
REFRESH_SECONDS = 1800    # rebuild v2/v3 indexes at most every 30 minutes
WEATHER_SECONDS = 3600


def file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else "missing"


def load_registry() -> dict:
    registry = json.loads(REGISTRY.read_text())
    if OVERRIDE.exists():
        try:
            override = json.loads(OVERRIDE.read_text())
            if override.get("champion") in registry["models"]:
                registry["champion"] = override["champion"]
        except (ValueError, OSError):
            pass
    return registry


class ModelRuntime:
    def __init__(self, fetch_weather: bool = True):
        self.lock = threading.Lock()
        self.registry = load_registry()
        self.v1 = trip_flow.load_model()
        self.m2022 = json.loads((ROOT / "models" / "historical_2022_transition.json").read_text())
        self.v4 = trip_flow_v4.load_v4()
        self.weather_table = weather.load()
        self.fetch_weather = fetch_weather
        self.history: dict[str, deque] = defaultdict(deque)
        self.network = trip_flow_v2.Network({})
        self.durations = trip_flow_v3.DurationIndex({})
        self.last_refresh = 0.0
        self.last_weather = 0.0
        self.weather_error: str | None = None
        self.versions = {name: file_hash(ROOT / spec["file"]) for name, spec in self.registry["models"].items()}

    # ---------- state -------------------------------------------------------------
    @property
    def champion(self) -> str:
        return self.registry["champion"]

    def model_version(self) -> str:
        return f"{self.champion}:{self.versions.get(self.champion, 'unknown')}"

    def load_history(self, snapshots: pathlib.Path) -> int:
        """Seed the rolling window from the tail of the snapshot file (startup only)."""
        if not snapshots.exists():
            return 0
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=HISTORY_DAYS)
        count = 0
        with snapshots.open() as handle:
            for line in handle:
                try:
                    frame = json.loads(line)
                    stamp = dt.datetime.fromisoformat(frame["collected_at"])
                except (ValueError, KeyError, TypeError):
                    continue
                if stamp >= cutoff:
                    self.observe(stamp, frame.get("stations", []), refresh=False)
                    count += 1
        self.refresh(force=True)
        return count

    def observe(self, stamp: dt.datetime, stations: list[dict], refresh: bool = True) -> None:
        cutoff = stamp - dt.timedelta(days=HISTORY_DAYS)
        with self.lock:
            for s in stations:
                compact = {k: s.get(k) for k in ("bikes", "docks", "renting", "returning", "last_reported")}
                series = self.history[str(s["station_id"])]
                series.append((stamp, compact))
                while series and series[0][0] < cutoff:
                    series.popleft()
        if refresh:
            self.refresh()

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last_refresh >= REFRESH_SECONDS:
            with self.lock:
                full = {sid: list(series) for sid, series in self.history.items()}
            recent_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=V2_HISTORY_HOURS)
            network = trip_flow_v2.Network({sid: [r for r in rows if r[0] >= recent_cut] for sid, rows in full.items()})
            durations = trip_flow_v3.DurationIndex(full)
            with self.lock:
                self.network, self.durations = network, durations
            self.last_refresh = now
        if self.fetch_weather and (force or now - self.last_weather >= WEATHER_SECONDS):
            self.last_weather = now
            try:
                payload = weather.fetch_forecast()
                with self.lock:
                    weather.merge_forecast(self.weather_table, payload)
                self.weather_error = None
            except Exception as exc:  # weather is optional; v4 falls back to v1 without it
                self.weather_error = f"{type(exc).__name__}: {exc}"

    # ---------- predictions -------------------------------------------------------
    def predict_all(self, station: dict, now: dt.datetime) -> dict[str, dict | None]:
        """All registered models' 30-minute risks for one live station record."""
        out: dict[str, dict | None] = {name: None for name in self.registry["models"]}
        if not (station.get("fresh") and station.get("renting") and station.get("returning")):
            return out
        sid, bikes, docks = station["id"], station.get("bikes"), station.get("docks")
        if bikes is None or docks is None:
            return out
        with self.lock:
            network, durations, table = self.network, self.durations, self.weather_table
            for name in out:
                try:
                    out[name] = self._predict(name, station, sid, bikes, docks, now, network, durations, table)
                except Exception:
                    out[name] = None  # one model failing never blocks the others
        return out

    def _predict(self, name, station, sid, bikes, docks, now, network, durations, table):
        if name == "model_2022":
            if station.get("name") not in self.m2022["trained_stations"]:
                return None
            return {"no_bike_30m": round(transition_predict(self.m2022["targets"]["no_bike"], bikes), 3),
                    "no_dock_30m": round(transition_predict(self.m2022["targets"]["no_dock"], docks), 3)}
        if name == "trip_flow_v1":
            result = trip_flow.predict(self.v1, sid, bikes, docks, now)
        elif name == "trip_flow_v2":
            result = trip_flow_v2.predict_v2(self.v1, network, sid, bikes, docks, now)
        elif name == "trip_flow_v3":
            result = trip_flow_v3.predict_v3(self.v1, durations, sid, bikes, docks, now)
        elif name == "trip_flow_v4":
            result = trip_flow_v4.predict_v4(self.v1, self.v4, table, sid, bikes, docks, now) if self.v4 else None
        else:
            return None
        if result is None:
            return None
        return {"no_bike_30m": result["no_bike_30m"], "no_dock_30m": result["no_dock_30m"]}

    def label(self) -> dict:
        spec = self.registry["models"][self.champion]
        return {"champion": self.champion, "label": spec["label"], "caveat": spec["caveat"],
                "model_version": self.model_version()}

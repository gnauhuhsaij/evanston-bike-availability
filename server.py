"""Local passenger prototype and prospective GBFS snapshot collector.

Run: python3 server.py; open http://127.0.0.1:8787
Only anonymous aggregate event counts are stored. No IP or user identifier is logged.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from analytics import DAILY, LOCAL, station_history, write_day
from pilot import ROOT
from train import count_bucket, transition_predict

GBFS_BASE = "https://gbfs.lyft.com/gbfs/2.3/chi/en/"
MODEL = ROOT / "models" / "historical_2022_transition.json"
SNAPSHOTS = ROOT / "data" / "raw" / "prospective_2026.jsonl"
NETWORK_DIR = pathlib.Path(os.environ.get("NETWORK_SNAPSHOTS_DIR", ROOT / "data" / "raw" / "network"))
EVENTS = ROOT / "data" / "anonymous_events.json"
STATIC = ROOT / "web"
POLL_SECONDS = 60
MAX_SOURCE_AGE = 300

state = {"stations": [], "last_success": None, "last_error": None, "polls": 0, "errors": 0, "last_poll_latency_ms": None, "fresh_station_count": 0,
         "network_last_success": None, "network_last_error": None, "network_station_count": 0}
network_written: dict[str, str] = {}  # day file -> fingerprint of the station list last written to it
lock = threading.Lock()


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "arrival-risk-research-prototype/0.1"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def write_network_snapshot(now: dt.datetime, info: dict, status: dict) -> int:
    """Append every Chicago station's status to a gzip file per Chicago calendar day.

    Compact rows: [bikes, docks, ebikes, bikes_disabled, docks_disabled, renting, returning,
    last_reported]. A "stations" line listing station ids and metadata precedes the first poll
    of each file and is repeated whenever the station list changes. Uses the feeds already
    fetched for Evanston; no extra requests. Reader: network_reader.py.
    """
    info_by_id = {s["station_id"]: s for s in info["data"]["stations"]}
    rows, ids = [], []
    for current in sorted(status["data"]["stations"], key=lambda s: s["station_id"]):
        meta = info_by_id.get(current["station_id"])
        if not meta:
            continue
        ids.append(current["station_id"])
        rows.append([current.get("num_bikes_available"), current.get("num_docks_available"),
                     current.get("num_ebikes_available"), current.get("num_bikes_disabled"),
                     current.get("num_docks_disabled"), int(current.get("is_renting") == 1),
                     int(current.get("is_returning") == 1), current.get("last_reported")])
    day = now.astimezone(LOCAL).date().isoformat()
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    path = NETWORK_DIR / f"{day}.jsonl.gz"
    fingerprint = hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]
    lines = []
    if network_written.get(path.name) != fingerprint:
        stations = [[i, info_by_id[i].get("short_name"), info_by_id[i].get("name"), info_by_id[i].get("lat"),
                     info_by_id[i].get("lon"), info_by_id[i].get("capacity"), info_by_id[i].get("region_id")] for i in ids]
        lines.append({"type": "stations", "t": now.isoformat(), "fingerprint": fingerprint,
                      "columns": ["station_id", "short_name", "name", "lat", "lon", "capacity", "region_id"], "stations": stations})
    lines.append({"type": "poll", "t": now.isoformat(), "src": status.get("last_updated"), "fingerprint": fingerprint,
                  "columns": ["bikes", "docks", "ebikes", "bikes_disabled", "docks_disabled", "renting", "returning", "last_reported"],
                  "rows": rows})
    payload = "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines).encode()
    with gzip.open(path, "ab") as handle:  # one gzip member per poll; readers handle concatenated members
        handle.write(payload)
    network_written[path.name] = fingerprint
    return len(rows)


def refresh() -> None:
    started = time.monotonic()
    info = fetch_json(GBFS_BASE + "station_information.json")
    status = fetch_json(GBFS_BASE + "station_status.json")
    status_by_id = {s["station_id"]: s for s in status["data"]["stations"]}
    model_bytes = MODEL.read_bytes() if MODEL.exists() else None
    model = json.loads(model_bytes) if model_bytes else None
    model_version = hashlib.sha256(model_bytes).hexdigest()[:12] if model_bytes else None
    now = dt.datetime.now(dt.timezone.utc)
    stations = []
    snapshot = []
    for item in info["data"]["stations"]:
        if item.get("region_id") != "35":
            continue
        current = status_by_id.get(item["station_id"])
        if not current:
            continue
        record = {
            "id": item["station_id"], "name": item["name"],
            "lat": item["lat"], "lon": item["lon"],
            "bikes": current.get("num_bikes_available"),
            "docks": current.get("num_docks_available"),
            "renting": current.get("is_renting") == 1,
            "returning": current.get("is_returning") == 1,
            "last_reported": current.get("last_reported"),
        }
        source_age = now.timestamp() - (record["last_reported"] or 0)
        record["fresh"] = 0 <= source_age <= MAX_SOURCE_AGE
        if model and record["fresh"] and record["renting"] and record["returning"] and record["name"] in model["trained_stations"]:
            record["experimental_risk"] = {
                "no_bike_30m": round(transition_predict(model["targets"]["no_bike"], record["bikes"]), 3),
                "no_dock_30m": round(transition_predict(model["targets"]["no_dock"], record["docks"]), 3),
            }
        stations.append(record)
        snapshot.append({"station_id": record["id"], "bikes": record["bikes"], "docks": record["docks"], "renting": record["renting"], "returning": record["returning"], "last_reported": record["last_reported"], "experimental_risk": record.get("experimental_risk")})
    SNAPSHOTS.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOTS.open("a") as file:
        file.write(json.dumps({"collected_at": now.isoformat(), "source_updated_at": status["last_updated"], "model_version": model_version, "stations": snapshot}) + "\n")
    try:  # Chicago-wide archive is best effort; it must never interrupt the Evanston collector.
        network_count = write_network_snapshot(now, info, status)
        with lock:
            state["network_last_success"], state["network_last_error"] = now.isoformat(), None
            state["network_station_count"] = network_count
    except Exception as exc:
        with lock:
            state["network_last_error"] = f"{type(exc).__name__}: {exc}"
        print(f"Network snapshot failed: {exc}", flush=True)
    with lock:
        state["stations"] = stations
        state["last_success"] = now.isoformat()
        state["last_error"] = None
        state["polls"] += 1
        state["last_poll_latency_ms"] = round((time.monotonic() - started) * 1000)
        state["fresh_station_count"] = sum(s["fresh"] for s in stations)


def poller() -> None:
    while True:
        try:
            refresh()
        except Exception as exc:
            with lock:
                state["last_error"] = str(exc)
                state["errors"] += 1
        time.sleep(POLL_SECONDS)


def daily_assessor() -> None:
    """Score yesterday after its final 30-minute outcomes can be observed."""
    while True:
        local_now = dt.datetime.now(LOCAL)
        if (local_now.hour, local_now.minute) >= (0, 40):
            day = local_now.date() - dt.timedelta(days=1)
            if not (DAILY / (day.isoformat() + ".json")).exists():
                try:
                    write_day(day)
                except Exception as exc:
                    print(f"Daily assessment failed for {day}: {exc}", flush=True)
        time.sleep(900)


def count_event(name: str) -> None:
    if name not in {"page_view", "station_select", "direction_pickup", "direction_return"}:
        return
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    with lock:
        data = json.loads(EVENTS.read_text()) if EVENTS.exists() else {}
        data.setdefault(day, {})[name] = data.setdefault(day, {}).get(name, 0) + 1
        EVENTS.parent.mkdir(exist_ok=True)
        EVENTS.write_text(json.dumps(data, indent=2) + "\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass  # Do not log visitor IP addresses.

    def send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/stations/") and parsed.path.endswith("/history"):
            station_id = parsed.path[len("/api/stations/"):-len("/history")].strip("/")
            window = parse_qs(parsed.query).get("window", ["day"])[0]
            if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", station_id) or window not in {"day", "month"}:
                self.send_error(400)
                return
            with lock:
                known = any(s["id"] == station_id for s in state["stations"])
            if not known:
                self.send_error(404)
                return
            self.send_json(station_history(station_id, window))
            return
        if self.path == "/api/stations":
            with lock:
                self.send_json({"stations": state["stations"], "last_success": state["last_success"], "error": state["last_error"], "model_status": "2022 backtest only; 2026 prospective validation pending"})
            return
        if self.path == "/api/health":
            with lock:
                ok = bool(state["last_success"]) and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(state["last_success"])).total_seconds() < 180
                self.send_json({"ok": ok, "polls": state["polls"], "errors": state["errors"], "last_success": state["last_success"], "station_count": len(state["stations"]), "fresh_station_count": state["fresh_station_count"], "last_poll_latency_ms": state["last_poll_latency_ms"], "network_last_success": state["network_last_success"], "network_last_error": state["network_last_error"], "network_station_count": state["network_station_count"]}, 200 if ok else 503)
            return
        if self.path == "/api/metrics":
            self.send_json({"anonymous_events_by_day": json.loads(EVENTS.read_text()) if EVENTS.exists() else {}})
            return
        relative = "index.html" if self.path == "/" else self.path.lstrip("/")
        if relative not in {"index.html", "style.css", "app.js", "charts.js", "leaflet.css", "leaflet.js"}:
            self.send_error(404)
            return
        target = STATIC / relative
        if not target.exists():
            self.send_error(404)
            return
        body = target.read_bytes()
        kind = "text/html" if relative.endswith("html") else "text/css" if relative.endswith("css") else "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/event":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1000:
                self.send_error(413)
                return
            payload = json.loads(self.rfile.read(length))
            name = payload.get("event")
            if name not in {"page_view", "station_select", "direction_pickup", "direction_return"}:
                self.send_error(400)
                return
            count_event(name)
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)


if __name__ == "__main__":
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=daily_assessor, daemon=True).start()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Open http://{host}:{port}", flush=True)
    server.serve_forever()

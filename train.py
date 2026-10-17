"""Chronological 30-minute stockout model and honest baseline evaluation.

Run after pilot.py. Uses the Python standard library so the experiment is
reproducible without a large ML runtime. The fitted weights are JSON.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import pathlib
import random
from collections import defaultdict

from pilot import RAW, ROOT, STATIONS, parse_time

DATA = RAW / "evanston_2022-08-01_09-07.json"
MODEL = ROOT / "models" / "historical_2022_logistic.json"
TRANSITION_MODEL = ROOT / "models" / "historical_2022_transition.json"
REPORT = ROOT / "data" / "model_metrics.json"
SPLIT = dt.datetime(2022, 9, 1)


def vector(row: dict, previous: dict | None) -> list[float]:
    t = parse_time(row["timestamp"])
    bikes = int(row["available_bikes"])
    docks = int(row["available_docks"])
    capacity = max(1, int(row.get("docks_in_service") or (bikes + docks)))
    hour = t.hour + t.minute / 60
    weekday = t.weekday()
    previous_bikes = int(previous["available_bikes"]) if previous else bikes
    previous_docks = int(previous["available_docks"]) if previous else docks
    return [
        1.0,
        min(bikes, 20) / 20,
        min(docks, 20) / 20,
        min(capacity, 40) / 40,
        1.0 if bikes == 0 else 0.0,
        1.0 if docks == 0 else 0.0,
        max(-10, min(10, bikes - previous_bikes)) / 10,
        max(-10, min(10, docks - previous_docks)) / 10,
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        1.0 if weekday >= 5 else 0.0,
        *[1.0 if row["station_name"] == name else 0.0 for name in STATIONS],
    ]


def examples(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["station_name"]].append(row)
    out = []
    for name, series in grouped.items():
        series.sort(key=lambda r: r["timestamp"])
        times = [parse_time(r["timestamp"]) for r in series]
        for i, row in enumerate(series):
            if row.get("status") != "In Service":
                continue
            target = times[i] + dt.timedelta(minutes=30)
            k = bisect.bisect_left(times, target)
            candidates = [j for j in (k - 1, k) if 0 <= j < len(series)]
            if not candidates:
                continue
            j = min(candidates, key=lambda j: abs((times[j] - target).total_seconds()))
            if j <= i or abs((times[j] - target).total_seconds()) > 300:
                continue
            future = series[j]
            if future.get("status") != "In Service":
                continue
            try:
                bikes, docks = int(future["available_bikes"]), int(future["available_docks"])
                now_bikes, now_docks = int(row["available_bikes"]), int(row["available_docks"])
            except (KeyError, ValueError, TypeError):
                continue
            previous = series[i-1] if i > 0 and (times[i] - times[i-1]).total_seconds() <= 900 else None
            out.append({
                "time": times[i], "name": name, "x": vector(row, previous),
                "now_bikes": now_bikes, "now_docks": now_docks,
                "no_bike": int(bikes == 0), "no_dock": int(docks == 0),
            })
    return sorted(out, key=lambda x: x["time"])


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    a = math.exp(z)
    return a / (1 + a)


def fit(train: list[dict], target: str, epochs: int = 15, learning_rate: float = 0.02) -> list[float]:
    weights = [0.0] * len(train[0]["x"])
    indices = list(range(len(train)))
    rng = random.Random(42)
    for epoch in range(epochs):
        rng.shuffle(indices)
        rate = learning_rate / (1 + epoch * 0.15)
        for i in indices:
            item = train[i]
            x, y = item["x"], item[target]
            p = sigmoid(sum(w * v for w, v in zip(weights, x)))
            error = p - y
            for k in range(len(weights)):
                weights[k] -= rate * (error * x[k] + (0.0001 * weights[k] if k else 0))
    return weights


def auc(y: list[int], p: list[float]) -> float | None:
    n1, n0 = sum(y), len(y) - sum(y)
    if not n1 or not n0:
        return None
    pairs = sorted(zip(p, y))
    rank_sum = 0.0
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        rank = (i + 1 + j) / 2
        rank_sum += rank * sum(label for _, label in pairs[i:j])
        i = j
    return round((rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0), 4)


def metrics(y: list[int], p: list[float]) -> dict:
    n = len(y)
    bins = []
    ece = 0.0
    for i in range(10):
        selected = [(truth, risk) for truth, risk in zip(y, p) if min(9, int(risk * 10)) == i]
        if not selected:
            continue
        mean_risk = sum(risk for _, risk in selected) / len(selected)
        observed = sum(truth for truth, _ in selected) / len(selected)
        ece += len(selected) / n * abs(mean_risk - observed)
        bins.append({"bin": i, "n": len(selected), "mean_predicted": round(mean_risk, 4), "observed": round(observed, 4)})
    return {
        "n": n, "positives": sum(y), "prevalence": round(sum(y) / n, 4),
        "brier": round(sum((risk - truth) ** 2 for truth, risk in zip(y, p)) / n, 5),
        "auc": auc(y, p), "ece_10_bins": round(ece, 4), "calibration": bins,
    }


def count_bucket(value: int) -> int:
    """Small-count transitions matter most; combine the sparse tail."""
    thresholds = (0, 1, 2, 3, 4, 5, 7, 10, 15)
    return next((i for i, threshold in enumerate(thresholds) if value <= threshold), len(thresholds))


def fit_transition(training: list[dict], target: str, current: str) -> dict:
    prevalence = sum(item[target] for item in training) / len(training)
    counts = defaultdict(lambda: [0, 0])
    for item in training:
        cell = counts[count_bucket(item[current])]
        cell[0] += item[target]
        cell[1] += 1
    return {"prevalence": prevalence, "counts": {str(bucket): pair for bucket, pair in counts.items()}, "smoothing": 10}


def transition_predict(model: dict, current_count: int) -> float:
    positive, total = model["counts"].get(str(count_bucket(current_count)), [0, 0])
    alpha = model["smoothing"]
    return (positive + alpha * model["prevalence"]) / (total + alpha)


def main() -> None:
    rows = json.loads(DATA.read_text())
    items = examples(rows)
    training = [item for item in items if item["time"] < SPLIT - dt.timedelta(minutes=30)]
    testing = [item for item in items if item["time"] >= SPLIT]
    if not training or not testing:
        raise RuntimeError("Chronological split yielded no data")
    results = {"train_window": "2022-08-01 through 2022-08-31", "test_window": "2022-09-01 through 2022-09-07", "train_n": len(training), "test_n": len(testing), "targets": {}}
    model = {"trained_on": results["train_window"], "features": ["bias", "bikes_20", "docks_20", "capacity_40", "zero_bikes", "zero_docks", "bike_delta_10", "dock_delta_10", "hour_sin", "hour_cos", "weekend", *["station:" + s for s in STATIONS]], "weights": {}}
    transition_model = {"trained_on": results["train_window"], "horizon_minutes": 30, "targets": {}}
    for target, current in (("no_bike", "now_bikes"), ("no_dock", "now_docks")):
        weights = fit(training, target)
        model["weights"][target] = weights
        transition = fit_transition(training, target, current)
        transition_model["targets"][target] = transition
        y = [item[target] for item in testing]
        predictions = [sigmoid(sum(w * v for w, v in zip(weights, item["x"]))) for item in testing]
        persistence = [float(item[current] == 0) for item in testing]
        prevalence = sum(item[target] for item in training) / len(training)
        climatology = [prevalence] * len(testing)
        transitions = [transition_predict(transition, item[current]) for item in testing]
        results["targets"][target] = {
            "model": metrics(y, predictions),
            "transition": metrics(y, transitions),
            "persistence": metrics(y, persistence),
            "training_prevalence": metrics(y, climatology),
            "by_station": {
                name: {
                    "transition": metrics([item[target] for item in testing if item["name"] == name], [p for item, p in zip(testing, transitions) if item["name"] == name]),
                    "persistence": metrics([item[target] for item in testing if item["name"] == name], [p for item, p in zip(testing, persistence) if item["name"] == name]),
                }
                for name in sorted({item["name"] for item in testing})
            },
        }
    MODEL.parent.mkdir(exist_ok=True)
    MODEL.write_text(json.dumps(model, indent=2) + "\n")
    TRANSITION_MODEL.write_text(json.dumps(transition_model, indent=2) + "\n")
    REPORT.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"train_n": len(training), "test_n": len(testing), "targets": {key: {name: {metric: val for metric, val in scores.items() if metric != "calibration"} for name, scores in group.items()} for key, group in results["targets"].items()}}, indent=2))


if __name__ == "__main__":
    main()

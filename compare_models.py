"""Compare 30-minute risk methods on the locally collected 2026 snapshots.

Methods, scored on identical origins:
  trip_flow   - v1, models/trip_flow.json (2025-10 .. 2026-09 trips; trained before any snapshot)
  trip_flow_v2 - v1 + hidden-demand, live-intensity and rebalancing corrections (trip_flow_v2.py),
                each estimated only from snapshots collected before the origin; the
                v2_censor / v2_intensity / v2_rebalance rows add one component at a time
  trip_flow_v3 - v1 + state-duration correction for already-empty/full stations (trip_flow_v3.py);
                its out-of-sample test is only the block "v3_since_freeze"
  trip_flow_v4 - v1 rates x weather multiplier (trip_flow_v4.py), coefficients fitted on the
                2025-10 .. 2026-09 trips only, so every snapshot is out of sample
  model_2022  - the deployed 2022 transition model, as recorded live in each snapshot
                (only the six stations with 2022 history), kept as a second baseline
  persistence - "the current state stays the same" baseline

Origin and label rules follow prospective.py: one origin per station per 10-minute
bucket, fresh (<=300 s) and in-service origin, outcome from the snapshot nearest to
+30 min within +-180 s that is fresh and in service. Nothing here tunes any model.

Usage: python3 compare_models.py      -> data/model_comparison.json
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import random
from collections import Counter, defaultdict

from analytics import LOCAL
from pilot import ROOT
from train import metrics
from trip_flow import load_model, predict
from trip_flow_v2 import Network, predict_v2
from trip_flow_v3 import DurationIndex, load_config as load_v3_config, predict_v3
from trip_flow_v4 import load_v4, predict_v4
import weather

SOURCE = ROOT / "data" / "raw" / "prospective_2026.jsonl"
OUTPUT = ROOT / "data" / "model_comparison.json"
TARGETS = (("no_bike", "bikes"), ("no_dock", "docks"))
V2_VARIANTS = {"trip_flow_v2": ("censor", "intensity", "rebalance"), "v2_censor": ("censor",),
               "v2_intensity": ("intensity",), "v2_rebalance": ("rebalance",)}
ALL_METHODS = ("trip_flow", *V2_VARIANTS, "trip_flow_v3", "trip_flow_v4", "persistence")


def bootstrap_diff(rows: list[dict], a: str, b: str, key: str, draws: int = 2000) -> dict | None:
    """Brier(a) - Brier(b) with a 95% interval from resampling whole station-days."""
    rows = [r for r in rows if r.get(a) and r.get(b)]
    if not rows:
        return None
    clusters: dict = {}
    for r in rows:
        clusters.setdefault((r["station_id"], r["date"]), []).append(r)
    groups = list(clusters.values())
    diff = lambda rs: sum((r[a][key] - r["truth"][key]) ** 2 - (r[b][key] - r["truth"][key]) ** 2 for r in rs) / len(rs)
    rng = random.Random(1)
    sims = sorted(diff([r for g in rng.choices(groups, k=len(groups)) for r in g]) for _ in range(draws))
    return {"diff": round(diff(rows), 5), "ci95": [round(sims[int(.025 * draws)], 5), round(sims[int(.975 * draws)], 5)],
            "clusters": len(groups)}


def brier_skill(score: dict | None, reference: dict | None) -> float | None:
    if not score or not reference or not reference["brier"]:
        return None
    return round(1 - score["brier"] / reference["brier"], 4)


def summarise(rows: list[dict], methods: tuple[str, ...]) -> dict:
    out = {}
    for key, _ in TARGETS:
        truth = [r["truth"][key] for r in rows]
        if not truth:
            out[key] = None
            continue
        block = {m: metrics(truth, [r[m][key] for r in rows]) for m in methods}
        block["brier_skill_vs_persistence"] = {m: brier_skill(block[m], block["persistence"]) for m in methods if m != "persistence"}
        out[key] = block
    return out


def main() -> None:
    model = load_model()
    if not model:
        raise SystemExit("Run python3 trip_flow.py fit first")
    by_station = defaultdict(list)
    polls = 0
    with SOURCE.open() as handle:
        for line in handle:
            try:
                frame = json.loads(line)
                stamp = dt.datetime.fromisoformat(frame["collected_at"])
            except (ValueError, KeyError, TypeError):
                continue
            polls += 1
            for station in frame.get("stations", []):
                by_station[str(station["station_id"])].append((stamp, station))
    network = Network(by_station)
    durations = DurationIndex(by_station)
    v3_config = load_v3_config()
    v3_frozen = dt.datetime.fromisoformat(v3_config["frozen_at"]) if v3_config else None
    v4_model, weather_table = load_v4(), weather.load()
    counts = Counter()
    rows = []
    for station_id, series in by_station.items():
        series.sort(key=lambda r: r[0])
        times = [r[0] for r in series]
        chosen = {}
        for stamp, station in series:
            chosen.setdefault(int(stamp.timestamp()) // 600, (stamp, station))
        for stamp, station in sorted(chosen.values(), key=lambda r: r[0]):
            counts["origin_buckets"] += 1
            age = stamp.timestamp() - (station.get("last_reported") or 0)
            if not 0 <= age <= 300 or not station.get("renting") or not station.get("returning") \
                    or station.get("bikes") is None or station.get("docks") is None:
                counts["origin_stale_or_closed"] += 1
                continue
            target = stamp + dt.timedelta(minutes=30)
            pos = bisect.bisect_left(times, target)
            nearby = [series[i] for i in (pos - 1, pos) if 0 <= i < len(series)]
            future = min(nearby, key=lambda r: abs((r[0] - target).total_seconds())) if nearby else None
            if not future or abs((future[0] - target).total_seconds()) > 180:
                counts["future_missing_or_pending"] += 1
                continue
            actual = future[1]
            actual_age = future[0].timestamp() - (actual.get("last_reported") or 0)
            if not 0 <= actual_age <= 300 or not actual.get("renting") or not actual.get("returning") \
                    or actual.get("bikes") is None or actual.get("docks") is None:
                counts["future_stale_or_closed"] += 1
                continue
            flow = predict(model, station_id, station["bikes"], station["docks"], stamp)
            if flow is None:
                counts["no_flow_prediction"] += 1
                continue
            old = station.get("experimental_risk")
            rows.append({
                "station_id": station_id,
                "date": stamp.astimezone(LOCAL).date().isoformat(),
                "time": stamp,
                "truth": {k: int(actual[c] == 0) for k, c in TARGETS},
                "persistence": {k: float(station[c] == 0) for k, c in TARGETS},
                "trip_flow": {k: flow[k + "_30m"] for k, _ in TARGETS},
                "model_2022": {k: old[k + "_30m"] for k, _ in TARGETS} if old else None,
            })
            for name, parts in V2_VARIANTS.items():
                v2 = predict_v2(model, network, station_id, station["bikes"], station["docks"], stamp, parts)
                rows[-1][name] = {k: v2[k + "_30m"] for k, _ in TARGETS}
            v3 = predict_v3(model, durations, station_id, station["bikes"], station["docks"], stamp)
            rows[-1]["trip_flow_v3"] = {k: v3[k + "_30m"] for k, _ in TARGETS}
            v4 = predict_v4(model, v4_model, weather_table, station_id, station["bikes"], station["docks"], stamp) if v4_model else flow
            rows[-1]["trip_flow_v4"] = {k: v4[k + "_30m"] for k, _ in TARGETS}
            if v4_model and v4.get("weather_missing"):
                counts["v4_weather_missing"] += 1
            counts["matched"] += 1
    six = [r for r in rows if r["model_2022"]]
    after_freeze = [r for r in rows if v3_frozen and r["time"] >= v3_frozen]
    names = {sid: e["name"] for sid, e in model["stations"].items()}
    per_day = defaultdict(list)
    for r in rows:
        per_day[r["date"]].append(r)
    result = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "trip_flow_trained_months": model["trained_months"],
        "polls": polls,
        "counts": dict(counts),
        "all_stations": {"n_origins": len(rows), "stations": len({r["station_id"] for r in rows}),
                         "scores": summarise(rows, ALL_METHODS)},
        "six_2022_stations": {"n_origins": len(six), "stations": sorted(names.get(s, s) for s in {r["station_id"] for r in six}),
                              "scores": summarise(six, ("trip_flow", "trip_flow_v2", "trip_flow_v4", "model_2022", "persistence"))},
        "by_day": {day: summarise(items, ("trip_flow", "trip_flow_v2", "persistence")) for day, items in sorted(per_day.items())},
        "v3_since_freeze": {
            "frozen_at": v3_config["frozen_at"] if v3_config else None,
            "n_origins": len(after_freeze),
            "scores": summarise(after_freeze, ("trip_flow", "trip_flow_v3", "persistence")),
            "brier_differences": {key: {"v3_minus_v1": bootstrap_diff(after_freeze, "trip_flow_v3", "trip_flow", key),
                                        "v3_minus_persistence": bootstrap_diff(after_freeze, "trip_flow_v3", "persistence", key)}
                                  for key, _ in TARGETS},
            "note": "Only this block is an out-of-sample test of v3; all-station v3 scores before frozen_at are biased.",
        },
        "brier_differences": {key: {
            "v2_minus_v1": bootstrap_diff(rows, "trip_flow_v2", "trip_flow", key),
            "v2_minus_persistence": bootstrap_diff(rows, "trip_flow_v2", "persistence", key),
            "v1_minus_persistence": bootstrap_diff(rows, "trip_flow", "persistence", key),
            "v2_minus_2022_six": bootstrap_diff(six, "trip_flow_v2", "model_2022", key),
            "v4_minus_v1": bootstrap_diff(rows, "trip_flow_v4", "trip_flow", key),
            "v4_minus_persistence": bootstrap_diff(rows, "trip_flow_v4", "persistence", key),
            "v4_minus_2022_six": bootstrap_diff(six, "trip_flow_v4", "model_2022", key),
        } for key, _ in TARGETS},
    }
    tmp = OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str) + "\n")
    tmp.replace(OUTPUT)

    def line(label, block, methods):
        if not block:
            print(f"  {label}: no data"); return
        parts = [f'{m} {block[m]["brier"]:.4f}' for m in methods]
        auc = [f'{m} {block[m]["auc"]}' for m in methods if m != "persistence"]
        print(f'  {label}: n={block[methods[0]]["n"]} positives={block[methods[0]]["positives"]} | Brier ' + ", ".join(parts) + " | AUC " + ", ".join(auc))
    print(f"polls={polls} counts={dict(counts)}")
    print(f"All {result['all_stations']['stations']} stations:")
    for key, _ in TARGETS:
        line(key, result["all_stations"]["scores"][key], ALL_METHODS)
    print(f"Six stations with 2022 model ({len(six)} origins):")
    for key, _ in TARGETS:
        line(key, result["six_2022_stations"]["scores"][key], ("trip_flow", "trip_flow_v2", "trip_flow_v4", "model_2022", "persistence"))
    block = result["v3_since_freeze"]
    print(f'v3 out-of-sample (origins since {block["frozen_at"]}): {block["n_origins"]} origins')
    for key, _ in TARGETS:
        line(key, block["scores"][key], ("trip_flow", "trip_flow_v3", "persistence"))
    print("Brier differences (negative = first is better), 95% station-day bootstrap:")
    for key, block in result["brier_differences"].items():
        for name, value in block.items():
            if value:
                print(f'  {key:8s} {name:22s} {value["diff"]:+.5f}  [{value["ci95"][0]:+.5f}, {value["ci95"][1]:+.5f}]')


if __name__ == "__main__":
    main()

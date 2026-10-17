"""Small operational gate for local/hosted health and prospective quality.

Exit 1 on actionable failures so a scheduler or uptime monitor can alert.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request
from pilot import ROOT

HEALTH = os.environ.get("HEALTH_URL", "http://127.0.0.1:8787/api/health")
PROSPECTIVE = ROOT / "data" / "prospective_metrics.json"


def main() -> int:
    failures = []
    try:
        with urllib.request.urlopen(HEALTH, timeout=5) as response:
            health = json.load(response)
    except Exception as exc:
        print(f"Health endpoint failed: {exc}")
        return 1
    if not health.get("ok"):
        failures.append("collector has no recent successful poll")
    stations = health.get("station_count", 0)
    fresh = health.get("fresh_station_count", 0)
    if stations and fresh / stations < 0.7:
        failures.append(f"only {fresh}/{stations} station reports are fresh")
    if (health.get("last_poll_latency_ms") or 0) > 10000:
        failures.append("GBFS polling exceeded 10 seconds")
    polls = health.get("polls", 0)
    if polls >= 10 and health.get("errors", 0) / (polls + health.get("errors", 0)) > 0.1:
        failures.append("GBFS polling error rate exceeds 10%")
    if PROSPECTIVE.exists():
        report = json.loads(PROSPECTIVE.read_text())
        for target, scores in report.get("targets", {}).items():
            if scores and scores["model"]["n"] >= 200 and scores["model"]["positives"] >= 10:
                if scores["model"]["brier"] > 1.2 * scores["persistence"]["brier"]:
                    failures.append(f"{target} prospective Brier is >20% worse than persistence")
    print(json.dumps({"health": health, "alerts": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

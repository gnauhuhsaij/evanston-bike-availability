# Dockside Evanston — Divvy arrival risk research prototype

An Evanston-first map of current bike and dock availability, with **experimental** 30-minute stockout risk for six stations. This is an independent research prototype. It is **not publicly deployed** and has **no verified real users**. The 2022 backtest does not establish 2026 accuracy. The current milestone is one complete day of local prospective measurement before making any release decision.

The measurement window and acceptance criteria are in [`reports/first-day-plan.md`](reports/first-day-plan.md).

## What is running

`python3 server.py` starts a local site at `http://127.0.0.1:8787`. The server polls the operator's public GBFS `station_information` and `station_status` feeds every 60 seconds, saves timestamped snapshots, and serves the map and API. It records only daily counts of page views and button actions, with no IP address or persistent visitor identifier. **The process and computer must remain running for collection to continue.**

On macOS, `python3 ops/local_collector.py install` runs the collector as a supervised user job, so it continues after the terminal or Codex turn closes and restarts after a process failure. Check it with `python3 ops/local_collector.py status`; stop it with `python3 ops/local_collector.py uninstall`. The job is active only in the current login session and cannot collect while the computer sleeps or is shut down.

Each station card initially shows its name, a horizontal blue/gray bar for bikes and open docks, the relevant 30-minute risk estimate, and the report age. "More station details" expands the history and rider guidance. History has a 24-hour or 30-day stacked area chart of **reported available bikes (lower area) and open docks (upper area)**, with missing reports shown as gaps. These are not trip or rider counts. A second curve shows daily 30-minute Brier score (lower is better) versus a current-state persistence baseline. Model scores exist only for the six historically matched stations; all other stations can show baseline scores, but the UI does not call them model accuracy.

After 00:40 Chicago time, the running server checks every 15 minutes and scores the previous calendar day's origins against reported states about 30 minutes later (±3-minute tolerance), then atomically saves `data/daily/YYYY-MM-DD.json`. Each report includes polls, coverage, per-station matching/missingness, model and baseline scores, positives, and calibration. The first startup day is marked partial. To rerun a completed day manually, use `python3 daily.py --date YYYY-MM-DD`. `python3 prospective.py` remains available for a cumulative, cross-day audit. The daily scheduler only runs while the server process is alive.

The six stations with historical overlap receive probabilities from a simple empirical state-transition model. The site labels these estimates as experimental, suppresses them for stale/offline stations, and directs riders to confirm with the official app.

## Reproduce the data audit and model

Python 3.12+ and internet access are required. No third-party Python packages are needed.

```bash
python3 pilot.py
python3 train.py
python3 -m unittest discover -s tests -v
python3 server.py
```

`pilot.py` downloads a small, bounded window from Chicago's historical station snapshots and compares 2022 station names/coordinates with the live GBFS station list. The raw response is cached in `data/raw/`, which is ignored by Git. `train.py` applies a chronological split: August 2022 training, September 1–7 testing. It writes model weights/tables to `models/` and metrics to `data/`. Both scripts can be rerun without changing code. The source URLs and sample window are explicit in `pilot.py`.

## Measured feasibility and historical results

See [`reports/feasibility.md`](reports/feasibility.md) for counts, missingness, definitions, caveats, and source terms. The machine-readable measurements are [`data/pilot_metrics.json`](data/pilot_metrics.json) and [`data/model_metrics.json`](data/model_metrics.json).

The 30-minute target is whether a station has zero available bikes or zero open docks about 30 minutes after an eligible in-service observation. A match requires a subsequent in-service snapshot within ±5 minutes. These are **reported station states**, not proof that a specific customer could rent or return a bike.

## Deployment and operations

The Dockerfile packages the site, poller, and daily assessor. A future hosted deployment would need persistent storage for `/app/data` so snapshots and reports survive restarts. Set `HOST=0.0.0.0` and `PORT` as required by the host. `python3 monitor.py` checks freshness, polling failures, yesterday's coverage, and sufficiently evidenced deterioration; it exits nonzero for an actionable alert. `/api/health` reports poll latency, errors, and source freshness; `/api/metrics` exposes anonymous daily usage counts. The GitHub Actions CI workflow runs checks and builds the container on push/PR. A separate, manually triggered release workflow only packages an image to GHCR; it does not deploy a site. Both workflows become active only after this project is pushed to GitHub. No hosting account or production deployment is configured yet, and the current local-data milestone does not include publishing.

Before a public release, collect recent data, hold out a future period that has not been used for tuning, and check calibration and improvement over the persistence baseline. One day can establish collection and label feasibility but may have too few zero-bike or zero-dock events to establish model quality. The currently displayed 2022 model is suitable only as a clearly labeled research preview.

## Data use and attribution

- [Divvy system data and official GBFS feed](https://divvybikes.com/system-data)
- [Divvy data license](https://divvybikes.com/data-license-agreement): grants analysis and product use through authorized interfaces, while also restricting "data mining or other extraction methods". The scope of that phrase for automated GBFS polling/model training is unclear; clarify it before a public release. It also prohibits republishing the raw data as a stand-alone dataset, customer reidentification, and implying affiliation or endorsement. Avoid operator trademarks and logos in the product name and page design.
- [Chicago historical station snapshots](https://data.cityofchicago.org/Transportation/Divvy-Bicycle-Stations-Historical/eq45-8inv). This dataset stopped updating in September 2022.
- [Leaflet 1.9.4](https://leafletjs.com/) is included in `web/` under its BSD license. Map tiles are © OpenStreetMap contributors.

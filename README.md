# Dockside Evanston — Divvy arrival risk research prototype

An Evanston-first map of current bike and dock availability, with **experimental** 30-minute stockout risk for six stations. This is an independent research prototype. It is **not publicly deployed** and has **no verified real users**. The 2022 backtest does not establish 2026 accuracy.

## What is running

`python3 server.py` starts a local site at `http://127.0.0.1:8787`. The server polls the operator's public GBFS `station_information` and `station_status` feeds every 60 seconds, saves timestamped snapshots, and serves the map and API. It records only daily counts of page views and button actions, with no IP address or persistent visitor identifier. Run `python3 prospective.py` after at least 30 minutes of uninterrupted snapshots to evaluate predictions against subsequent observed station states. The server process must stay running for collection to continue.

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

The Dockerfile packages the site and poller. A hosted deployment must mount `/app/data` on persistent storage so snapshots and anonymous event counts survive restarts. Set `HOST=0.0.0.0` and `PORT` as required by the host. A scheduler should run `python3 prospective.py` at least daily and alert if `/api/health` reports `ok=false`, if the GBFS error count rises, or if fresh station coverage drops. `/api/health` reports poll latency, errors, and source freshness; `/api/metrics` exposes anonymous daily usage counts. The GitHub Actions workflow runs code checks and builds the container on push/PR; it becomes active only after this project is pushed to GitHub. No hosting account or production deployment is configured yet.

Before a public release, collect recent data, hold out a future period that has not been used for tuning, and confirm calibration and improvement over the persistence baseline. The currently displayed 2022 model is suitable only as a clearly labeled research preview.

## Data use and attribution

- [Divvy system data and official GBFS feed](https://divvybikes.com/system-data)
- [Divvy data license](https://divvybikes.com/data-license-agreement): permits use in a product or service, prohibits republishing the raw data as a stand-alone dataset, unauthorized access, customer reidentification, and implying affiliation or endorsement. Avoid operator trademarks and logos in the product name and page design.
- [Chicago historical station snapshots](https://data.cityofchicago.org/Transportation/Divvy-Bicycle-Stations-Historical/eq45-8inv). This dataset stopped updating in September 2022.
- [Leaflet 1.9.4](https://leafletjs.com/) is included in `web/` under its BSD license. Map tiles are © OpenStreetMap contributors.

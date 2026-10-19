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

## Recent trip-flow model (candidate) and baselines

`trip_flow.py` learns, for each of the 27 Evanston stations, how many bikes leave and arrive per hour (weekday/weekend) from the operator's monthly trip files, 2025-10 through 2026-09. Rates are seasonally adjusted with a calendar-month factor computed from the 14 stations open all year, stations that opened in mid-2026 are averaged only over the days since their first trip, and sparse station-hours are shrunk toward the Evanston hourly profile (10 pseudo-days). A birth-death chain bounded by current bikes + open docks turns the current reported stock into 30-minute probabilities of zero bikes or zero open docks. Rebalancing, dockless e-bike parking and turned-away riders are not visible in trip records.

```bash
python3 trip_flow.py count all   # trip CSVs in data/raw/ -> data/trip_counts/
python3 trip_flow.py fit         # -> models/trip_flow.json
python3 compare_models.py        # -> data/model_comparison.json
```

`trip_flow_v2.py` (config `models/trip_flow_v2.json`) keeps v1's rates and adds three corrections estimated at prediction time from snapshots collected strictly before that time: hidden demand (rates divided by the share of earlier polls with a bike or an open dock), live intensity (last 60 minutes of observed departures/arrivals vs v1's expectation, Gamma-Poisson shrunk toward 1) and rebalancing (single-poll changes of 4+ bikes treated as truck visits, added to the chain as jumps). Its constants were fixed before scoring. `compare_models.py` reports v2 and each component separately next to v1, the 2022 model and persistence, with station-day bootstrap intervals for the key Brier differences. v1 and the 2022 model are unchanged.

`trip_flow_v3.py` (config `models/trip_flow_v3.json`, frozen 2026-10-18 22:56 UTC) adds a state-duration correction: for a station already empty (or full), the probability it is still empty (full) 30 minutes later is shrunk toward the pooled rate observed for stations stuck that long (bins 0-15, 15-60, 60-180, 180+ minutes), using only outcomes known before the prediction. Because the idea came from 16-18 October data, only origins after the freeze time count as its test (`v3_since_freeze` in the comparison).

`trip_flow_v4.py` (`models/trip_flow_v4.json`) multiplies v1's rates by a weather factor from a Poisson regression of hourly Evanston trips on cold, heat, rain (now / heavy / previous 2 hours), snow (falling / on the ground) and wind, with v1's expected count as the offset. It was fitted on trips only (2025-10 .. 2026-09), so every snapshot is out of sample; a Jul-Sep holdout is printed by `python3 trip_flow_v4.py fit`. Hourly weather is in `data/weather/evanston_hourly.csv`, from [Open-Meteo](https://open-meteo.com/) (data under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); weather data by Open-Meteo.com), and `weather.py` can fetch current hours from its forecast API for live use.

The collector also archives every Chicago station's status (including e-bikes and disabled bikes/docks) from the same two feed requests into `data/raw/network/YYYY-MM-DD.jsonl.gz` (git-ignored, about 10 MB/day); `network_reader.py` reads it. The Evanston snapshot file is unchanged. The new collector code takes effect at the next collector restart.

`compare_models.py` scores the trip-flow model, the deployed 2022 transition model (kept as a second baseline, six stations) and the current-state persistence baseline on identical prospective origins, using the same origin and outcome rules as `prospective.py`. The trip data ends before the first snapshot, so this is an out-of-time test; no parameters were tuned on it. The live site still serves the 2022 model; switching requires a server restart and should wait until the comparison has enough events. The station mapping `data/evanston_stations.json` (GBFS `station_id` to trip-file `short_name`) was captured from the public station_information feed on 2026-10-18.

## Deployment (AWS)

The site and collector run as one container on a small EC2 server (`us-east-2`). Collected data lives on a separate disk mounted at `/srv/dockside/data` (inside the container: `/app/data`), so deploys and rollbacks never touch it. One-time AWS setup: the "Dockside AWS setup runbook" doc.

- **Push to `main`** runs `.github/workflows/deploy.yml`: tests, builds an ARM image tagged with the commit, pushes it to ECR (keyless login through GitHub OIDC), and runs `deploy/deploy.sh` on the server through AWS Systems Manager. The script stops the old container, starts the new one, and checks health, a fresh snapshot with logged predictions, and champion probabilities within [0, 1]. On failure it restarts the previous version and the workflow fails.
- **Manual rollback**: Actions → Deploy → Run workflow, with an earlier full commit SHA as `image_tag`.
- **Manual daily assessment** (recovery after downtime): Actions → "Daily assessment (manual recovery)" with a date.
- **Champion / challengers**: `models/registry.json` names the model shown on the site (`trip_flow_v1`); all models are computed every poll and logged under `predictions` in each snapshot for prospective scoring. The rule for replacing the champion is in `reports/model-promotion.md`.
- **Chicago-wide archive** is off by default (`NETWORK_ARCHIVE=off`) pending the data-license question; enable with a line `NETWORK_ARCHIVE=on` in `/srv/dockside/state/env` and a redeploy.
- **Private access**: the container listens only on the server's localhost; open it with an SSM port-forwarding session (see the runbook).

## Data use and attribution

- [Divvy system data and official GBFS feed](https://divvybikes.com/system-data)
- [Divvy data license](https://divvybikes.com/data-license-agreement): grants analysis and product use through authorized interfaces, while also restricting "data mining or other extraction methods". The scope of that phrase for automated GBFS polling/model training is unclear; clarify it before a public release. It also prohibits republishing the raw data as a stand-alone dataset, customer reidentification, and implying affiliation or endorsement. Avoid operator trademarks and logos in the product name and page design.
- [Chicago historical station snapshots](https://data.cityofchicago.org/Transportation/Divvy-Bicycle-Stations-Historical/eq45-8inv). This dataset stopped updating in September 2022.
- [Leaflet 1.9.4](https://leafletjs.com/) is included in `web/` under its BSD license. Map tiles are © OpenStreetMap contributors.

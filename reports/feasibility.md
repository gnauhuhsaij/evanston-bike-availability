# Data feasibility and first backtest — 2026-10-16 Chicago time

## Decision

The 30-minute **reported station stockout** label is feasible for the historical six-station pilot. The 15-minute label is less complete because historical snapshots are roughly ten minutes apart. The project can proceed as a research prototype, while recent prospective collection remains a gate before treating 2026 risk estimates as validated.

## Audit of source and matching

Source: [Chicago historical station snapshots](https://data.cityofchicago.org/Transportation/Divvy-Bicycle-Stations-Historical/eq45-8inv), queried for eight selected Evanston names from 2022-08-01 through 2022-09-07 inclusive; matched against [current official GBFS](https://divvybikes.com/system-data). The extraction occurred 2026-10-16 Chicago time.

| Measurement | Observed |
|---|---:|
| Historical rows extracted | 32,376 |
| Selected stations with historical rows | 6 / 8 |
| Historical stations matching a current name and coordinate | 6 / 6, all 0 m reported distance |
| Eligible in-service origin rows | 32,375 |
| 15-minute matched labels, target ±5 min | 27,287 / 32,375 = 84.28% |
| 30-minute matched labels, target ±5 min | 31,906 / 32,375 = 98.55% |
| Missing 30-minute future observation | 468 |
| 30-minute future out of service | 1 |
| 30-minute no-bike labels | 2,149 |
| 30-minute no-dock labels | 479 |

Two current stations, Dempster CTA and Robert Crown Community Center, had no rows in this sampled historical week. They receive current availability on the map but no risk estimate. IDs changed between historical and current feeds, so the historical-to-current join uses exact station name **and coordinates**, never an assumed ID equality. This small pilot does not prove the match rate for all 27 current Evanston stations.

## Chronological model experiment

Train: 2022-08-01 through 2022-08-31, 26,028 labeled examples. Test: 2022-09-01 through 2022-09-07, 5,860 labeled examples. A 30-minute gap is left at the split so training labels cannot use September outcomes. The persistence baseline predicts 100% risk if the station is already empty/full and 0% otherwise. The transition model learns smoothed probabilities from current bike/dock count buckets. A logistic model was also tried and retained in the metrics file, though it underperformed persistence on Brier score.

| Target on 2022 test | Events | Persistence Brier ↓ | Transition Brier ↓ | Transition AUC | Transition ECE, 10 bins ↓ |
|---|---:|---:|---:|---:|---:|
| No bike after 30 min | 607 / 5,860 | 0.02560 | 0.02371 | 0.9740 | 0.0052 |
| No dock after 30 min | 27 / 5,860 | 0.00205 | 0.00164 | 0.9991 | 0.0006 |

The no-dock test has only 27 events, all at two stations; the impressive AUC is unstable and must not be advertised as generalized performance. The no-bike transition model beats persistence overall but loses at Central St Metra (Brier 0.02068 vs 0.01945). By-station calibration and metrics are in `data/model_metrics.json`. The transition model configuration was inspected during exploratory testing; a genuinely untouched future period is still required before claiming a confirmed improvement. No real usage or avoided-failure effect has been measured.

## Operational caveats

- Historical snapshots stop in September 2022. Current stations, fleet mix, riding habits, and docking rules may have changed.
- The label is station-reported inventory, not a completed rental or dock attempt. GBFS counts can lag and inoperative stations are excluded.
- A 30-minute target is sampled within ±5 minutes, introducing timing uncertainty. Fifteen-minute coverage is only 84.28% under the same rule.
- Current feed quality is monitored by source timestamp and active status; stale stations receive no model score.
- Prospective data is being gathered only while the local server is running. `prospective.py` will report future match rate, missingness, calibration, and Brier score when enough new outcomes accrue.

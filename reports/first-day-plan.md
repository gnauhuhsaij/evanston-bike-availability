# First complete prospective day

The collector began on October 16, 2026 at 21:46 Chicago time. October 16 is an initial partial day. If the process and computer keep running, the first complete Chicago calendar day is October 17. Its report is generated after 00:40 on October 18 by the server's 15-minute scheduler. The daily Codex follow-up checks the result later that morning.

## Measurements to report

1. Poll coverage: observed polls divided by the expected 1,440 minute polls for the date. At least 90% is the data-quality target; outages remain visible in the report and the chart.
2. Label matching: per station, count eligible 10-minute origin buckets, matched 30-minute outcomes, stale/closed station reports, and missing futures. Outcomes are reported station states within ±3 minutes of the target.
3. Prospective model quality: for each of the six modeled stations and each target, show matched `n`, positive events, Brier score, calibration bins, and the persistence baseline scored on the same origins. All 27 stations receive a baseline evaluation when labels are available. The other 21 stations have no model accuracy claim yet.
4. Interpretation: one day can validate collection and matching. If zero-bike or zero-dock events are scarce, probability calibration and superiority to baseline remain inconclusive. Do not tune the historical model on the first-day holdout before reporting its prospective result.

The 24-hour and 30-day station charts show reported available bikes and open docks, not trips or unique riders. A 30-day curve accumulates only as those days are actually collected; no historical line is filled with invented measurements.

This milestone is local validation only. There is no public deployment or verified outside usage yet. The [operator's data license](https://divvybikes.com/data-license-agreement) permits products using the data but also restricts “data mining or other extraction methods”; clarify applicability to ongoing GBFS model evaluation before a public release.

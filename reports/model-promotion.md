# Model promotion rule

Written 2026-10-18, before any challenger was eligible, so the decision cannot drift toward whichever
result looks best on the day.

**Champion** (shown on the site): `trip_flow_v1`. **Challengers** (computed and logged in every
snapshot under `predictions`, never shown): `model_2022`, `trip_flow_v2`, `trip_flow_v3`, `trip_flow_v4`.

A challenger replaces the champion only when, over at least **14 complete Chicago days that include
weekdays**, scored by `compare_models.py` on origins after the challenger was frozen:

1. Its Brier score beats the champion on **both** targets (no bike, no dock), and the 95% station-day
   bootstrap interval of the difference excludes zero for both.
2. Its calibration is no worse than the champion's (mean predicted vs observed rate, within the same bins).
3. It still beats the persistence baseline on both targets.
4. It produces an estimate for at least as many stations as the champion.

Switching: change `champion` in `models/registry.json`, commit, push (deploys automatically). Emergency
switch without a deploy: write `{"champion": "<name>"}` to `/srv/dockside/data/registry_override.json`
and restart the container. Any change to a challenger's code or parameters makes it a new version with
its own freeze date; earlier scores do not carry over.

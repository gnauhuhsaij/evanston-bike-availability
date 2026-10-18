import datetime as dt
import unittest

from trip_flow import predict
from trip_flow_v3 import DurationIndex, duration_bin, predict_v3

UTC = dt.timezone.utc
FLAT = {"weekday": [3.0] * 24, "weekend": [3.0] * 24}
V1 = {"month_factors": {"10": 1.0}, "horizon_hours": 0.5,
      "stations": {"s1": {"departures_per_hour": FLAT, "arrivals_per_hour": FLAT},
                   "s2": {"departures_per_hour": FLAT, "arrivals_per_hour": FLAT}}}
START = dt.datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def poll(minute, bikes, docks):
    stamp = START + dt.timedelta(minutes=minute)
    return stamp, {"bikes": bikes, "docks": docks, "renting": True, "returning": True, "last_reported": stamp.timestamp() - 20}


class DurationTests(unittest.TestCase):
    def test_bins(self):
        self.assertEqual([duration_bin(m) for m in (0, 14.9, 15, 59, 60, 179, 180, 600)], [0, 0, 1, 1, 2, 2, 3, 3])

    def test_station_not_empty_or_full_keeps_v1(self):
        series = {"s1": [poll(m, 4, 6) for m in range(0, 120)]}
        when = START + dt.timedelta(minutes=119)
        v3 = predict_v3(V1, DurationIndex(series), "s1", 4, 6, when)
        v1 = predict(V1, "s1", 4, 6, when)
        self.assertEqual((v3["no_bike_30m"], v3["no_dock_30m"]), (v1["no_bike_30m"], v1["no_dock_30m"]))

    def test_long_stuck_history_raises_stay_probability(self):
        # s2 sits empty for 6 hours: many past long-empty origins all stayed empty.
        series = {"s2": [poll(m, 0, 10) for m in range(0, 400)], "s1": [poll(m, 0, 10) for m in range(0, 400)]}
        when = START + dt.timedelta(minutes=399)
        v3 = predict_v3(V1, DurationIndex(series), "s1", 0, 10, when)
        v1 = predict(V1, "s1", 0, 10, when)
        self.assertGreater(v3["no_bike_30m"], v1["no_bike_30m"])
        self.assertGreater(v3["info"]["no_bike"]["seen"], 0)

    def test_future_snapshots_do_not_change_past_prediction(self):
        past = [poll(m, 0, 10) for m in range(0, 200)]
        future = [poll(m, 8, 2) for m in range(200, 400)]
        when = START + dt.timedelta(minutes=199)
        a = predict_v3(V1, DurationIndex({"s1": past}), "s1", 0, 10, when)
        b = predict_v3(V1, DurationIndex({"s1": past + future}), "s1", 0, 10, when)
        self.assertEqual(a, b)

    def test_gap_restarts_duration_clock(self):
        series = {"s1": [poll(m, 0, 10) for m in range(0, 60)] + [poll(m, 0, 10) for m in range(90, 95)]}
        minutes = DurationIndex(series).state_minutes("s1", START + dt.timedelta(minutes=94))
        self.assertLess(minutes[0], 15)


if __name__ == "__main__":
    unittest.main()

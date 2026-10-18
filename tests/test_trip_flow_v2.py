import datetime as dt
import unittest

from trip_flow import predict
from trip_flow_v2 import Network, predict_v2, stock_distribution_jumps

UTC = dt.timezone.utc
FLAT = {"weekday": [2.0] * 24, "weekend": [2.0] * 24}
V1 = {"month_factors": {"10": 1.0}, "horizon_hours": 0.5,
      "stations": {"s1": {"departures_per_hour": FLAT, "arrivals_per_hour": FLAT}}}


def poll(minute, bikes, docks, start=dt.datetime(2026, 10, 14, 15, 0, tzinfo=UTC)):
    stamp = start + dt.timedelta(minutes=minute)
    return stamp, {"bikes": bikes, "docks": docks, "renting": True, "returning": True,
                   "last_reported": stamp.timestamp() - 20}


class JumpChainTests(unittest.TestCase):
    def test_no_jumps_matches_v1(self):
        now = dt.datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
        network = Network({"s1": [poll(0, 3, 5)]})
        v2 = predict_v2(V1, network, "s1", 3, 5, now, use=())
        v1 = predict(V1, "s1", 3, 5, now)
        self.assertAlmostEqual(v2["no_bike_30m"], v1["no_bike_30m"], places=4)
        self.assertAlmostEqual(v2["no_dock_30m"], v1["no_dock_30m"], places=4)

    def test_truck_drop_off_lowers_empty_risk(self):
        without = stock_distribution_jumps(1, 10, 3.0, 0.5, 0.0, 0.0, 5, 0.5)
        with_truck = stock_distribution_jumps(1, 10, 3.0, 0.5, 2.0, 0.0, 5, 0.5)
        self.assertLess(with_truck[0], without[0])
        self.assertAlmostEqual(sum(with_truck), 1.0, places=9)


class OnlineTests(unittest.TestCase):
    def test_future_snapshots_do_not_change_past_prediction(self):
        past = [poll(m, 6 - m // 10, 4 + m // 10) for m in range(0, 60)]
        future = [poll(m, 0, 10) for m in range(61, 120)] + [poll(121, 10, 0)]
        when = past[-1][0] + dt.timedelta(seconds=30)
        a = predict_v2(V1, Network({"s1": past}), "s1", 1, 9, when)
        b = predict_v2(V1, Network({"s1": past + future}), "s1", 1, 9, when)
        self.assertEqual(a, b)

    def test_recent_heavy_departures_raise_empty_risk(self):
        quiet = [poll(m, 8, 2) for m in range(0, 60)]
        busy = [poll(m, 8 - m // 10, 2 + m // 10) for m in range(0, 60)]  # 5 bikes left in an hour
        when = quiet[-1][0] + dt.timedelta(seconds=30)
        calm = predict_v2(V1, Network({"s1": quiet}), "s1", 3, 7, when, use=("intensity",))
        surge = predict_v2(V1, Network({"s1": busy}), "s1", 3, 7, when, use=("intensity",))
        self.assertGreater(surge["no_bike_30m"], calm["no_bike_30m"])

    def test_large_single_poll_change_counts_as_truck_not_riders(self):
        series = [poll(m, 2, 8) for m in range(0, 30)] + [poll(30, 9, 1)] + [poll(m, 9, 1) for m in range(31, 60)]
        when = series[-1][0] + dt.timedelta(seconds=30)
        info = predict_v2(V1, Network({"s1": series}), "s1", 9, 1, when)["info"]
        self.assertGreater(info["rebalance"][0], 0)       # one truck drop-off observed
        self.assertLessEqual(info["intensity"][1], 1.0)   # the +7 is not counted as rider arrivals


if __name__ == "__main__":
    unittest.main()

import datetime as dt
import unittest

from trip_flow import predict, stock_distribution, window_rates


class StockDistributionTests(unittest.TestCase):
    def test_zero_rates_keep_current_state(self):
        self.assertEqual(stock_distribution(3, 5, 0, 0, 0.5), [0, 0, 0, 1.0, 0, 0])

    def test_probabilities_sum_to_one_and_stay_in_bounds(self):
        dist = stock_distribution(2, 10, 4.0, 1.5, 0.5)
        self.assertAlmostEqual(sum(dist), 1.0, places=9)
        self.assertTrue(all(0 <= p <= 1 for p in dist))

    def test_departures_only_matches_poisson_tail(self):
        # With only departures, P(empty) = P(Poisson(rate*t) >= bikes).
        import math
        mean = 3.0 * 0.5
        expected = 1 - sum(math.exp(-mean) * mean ** k / math.factorial(k) for k in range(2))
        self.assertAlmostEqual(stock_distribution(2, 8, 3.0, 0.0, 0.5)[0], expected, places=9)

    def test_full_station_with_arrivals_only_stays_full(self):
        self.assertAlmostEqual(stock_distribution(6, 6, 0.0, 5.0, 0.5)[6], 1.0, places=9)


class PredictTests(unittest.TestCase):
    def setUp(self):
        flat = {"weekday": [2.0] * 24, "weekend": [2.0] * 24}
        self.model = {"month_factors": {"10": 1.0}, "horizon_hours": 0.5,
                      "stations": {"s1": {"departures_per_hour": flat, "arrivals_per_hour": flat}}}

    def test_unknown_station_or_zero_capacity_returns_none(self):
        now = dt.datetime(2026, 10, 17, 15, tzinfo=dt.timezone.utc)
        self.assertIsNone(predict(self.model, "nope", 3, 3, now))
        self.assertIsNone(predict(self.model, "s1", 0, 0, now))

    def test_symmetric_rates_give_symmetric_risks(self):
        now = dt.datetime(2026, 10, 17, 15, tzinfo=dt.timezone.utc)
        risk = predict(self.model, "s1", 4, 4, now)
        self.assertAlmostEqual(risk["no_bike_30m"], risk["no_dock_30m"], places=4)

    def test_window_crosses_hour_boundary(self):
        entry = {"departures_per_hour": {"weekday": [0.0] * 14 + [4.0] + [0.0] * 9, "weekend": [0.0] * 24},
                 "arrivals_per_hour": {"weekday": [0.0] * 24, "weekend": [0.0] * 24}}
        start = dt.datetime(2026, 10, 14, 13, 45)  # a Wednesday, local time
        dep, _ = window_rates(entry, 1.0, start, 0.5)
        self.assertAlmostEqual(dep, 2.0)  # 15 of the 30 minutes fall in the 4/hour hour


if __name__ == "__main__":
    unittest.main()

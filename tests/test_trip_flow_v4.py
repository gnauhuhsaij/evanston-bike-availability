import datetime as dt
import unittest

import weather
from trip_flow import predict
from trip_flow_v4 import fit_poisson, predict_v4

UTC = dt.timezone.utc
FLAT = {"weekday": [3.0] * 24, "weekend": [3.0] * 24}
V1 = {"month_factors": {"10": 1.0}, "horizon_hours": 0.5,
      "stations": {"s1": {"departures_per_hour": FLAT, "arrivals_per_hour": FLAT}}}
WHEN = dt.datetime(2026, 10, 14, 17, 10, tzinfo=UTC)          # 12:10 local
LOCAL_HOUR = "2026-10-14T12:00"


def table(precip=0.0, temp=18.0):
    hours = {}
    for back in range(0, 3):
        stamp = (dt.datetime(2026, 10, 14, 12) - dt.timedelta(hours=back)).strftime("%Y-%m-%dT%H:00")
        hours[stamp] = {"temperature_c": temp, "precipitation_mm": precip if back == 0 else 0.0, "snowfall_cm": 0.0,
                        "snow_depth_m": 0.0, "wind_kmh": 10.0, "weather_code": 0.0}
    return hours


class FeatureTests(unittest.TestCase):
    def test_feature_vector(self):
        x = weather.features(table(precip=3.0, temp=5.0), dt.datetime(2026, 10, 14, 12, 10))
        self.assertEqual(len(x), len(weather.FEATURES))
        self.assertEqual(x[weather.FEATURES.index("rain_now")], 1.0)
        self.assertEqual(x[weather.FEATURES.index("heavy_rain_now")], 1.0)
        self.assertAlmostEqual(x[weather.FEATURES.index("cold_below_10c")], 0.5)

    def test_missing_hour_returns_none(self):
        self.assertIsNone(weather.features({}, dt.datetime(2026, 10, 14, 12)))


class V4Tests(unittest.TestCase):
    def test_zero_coefficients_equal_v1(self):
        v4 = {"beta": [0.0] * len(weather.FEATURES)}
        a = predict_v4(V1, v4, table(), "s1", 2, 8, WHEN)
        b = predict(V1, "s1", 2, 8, WHEN)
        self.assertEqual((a["no_bike_30m"], a["no_dock_30m"]), (b["no_bike_30m"], b["no_dock_30m"]))

    def test_rain_slows_turnover(self):
        beta = [0.0] * len(weather.FEATURES)
        beta[weather.FEATURES.index("rain_now")] = -0.5
        v4 = {"beta": beta}
        dry = predict_v4(V1, v4, table(), "s1", 1, 9, WHEN)
        wet = predict_v4(V1, v4, table(precip=1.0), "s1", 1, 9, WHEN)
        self.assertLess(wet["no_bike_30m"], dry["no_bike_30m"])
        self.assertLess(wet["weather_multiplier"], 1.0)

    def test_missing_weather_falls_back_to_v1(self):
        v4 = {"beta": [0.3] * len(weather.FEATURES)}
        a = predict_v4(V1, v4, {}, "s1", 2, 8, WHEN)
        b = predict(V1, "s1", 2, 8, WHEN)
        self.assertTrue(a["weather_missing"])
        self.assertEqual(a["no_bike_30m"], b["no_bike_30m"])

    def test_poisson_fit_recovers_known_effect(self):
        rows = []
        for i in range(400):
            rain = float(i % 4 == 0)
            expected = 20.0
            rows.append({"x": [1.0, rain], "expected": expected, "y": round(expected * (0.5 if rain else 1.0))})
        beta, _ = fit_poisson(rows, ridge=0.0)
        self.assertAlmostEqual(beta[0], 0.0, places=3)
        self.assertAlmostEqual(beta[1], -0.6931, places=3)


if __name__ == "__main__":
    unittest.main()

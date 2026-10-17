import datetime as dt
import unittest

from train import examples, metrics, transition_predict


def row(time, bikes, docks, status="In Service"):
    return {"station_name": "Benson Ave & Church St", "timestamp": time, "available_bikes": str(bikes), "available_docks": str(docks), "docks_in_service": "10", "status": status}


class PipelineTests(unittest.TestCase):
    def test_label_uses_forward_snapshot_with_tolerance(self):
        rows = [
            row("2022-09-01T09:00:00", 2, 8),
            row("2022-09-01T09:10:00", 1, 9),
            row("2022-09-01T09:29:00", 0, 10),
            row("2022-09-01T09:50:00", 0, 10),
        ]
        data = examples(rows)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["time"], dt.datetime(2022, 9, 1, 9))
        self.assertEqual(data[0]["no_bike"], 1)

    def test_closed_future_is_not_labeled_as_stockout(self):
        rows = [row("2022-09-01T09:00:00", 2, 8), row("2022-09-01T09:30:00", 0, 0, "Not In Service")]
        self.assertEqual(examples(rows), [])

    def test_probability_model_is_not_deterministic_at_zero_count(self):
        model = {"counts": {"0": [8, 10]}, "prevalence": 0.1, "smoothing": 10}
        self.assertAlmostEqual(transition_predict(model, 0), 0.45)

    def test_calibration_report_has_measured_outcomes(self):
        result = metrics([0, 1, 0, 1], [0.1, 0.9, 0.2, 0.8])
        self.assertEqual(result["positives"], 2)
        self.assertEqual(result["n"], 4)
        self.assertLess(result["brier"], 0.03)


if __name__ == "__main__":
    unittest.main()

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analytics


class DailyAssessmentTests(unittest.TestCase):
    def test_scoring_uses_future_report_and_keeps_unmodeled_station_separate(self):
        day = dt.date(2026, 10, 16)
        def frame(minute, bikes, station_id, modeled):
            stamp = dt.datetime(2026, 10, 16, 16, minute, tzinfo=dt.timezone.utc)
            return {"collected_at": stamp.isoformat(), "model_version": "abc" if modeled else None,
                    "stations": [{"station_id": station_id, "bikes": bikes, "docks": 4,
                                  "renting": True, "returning": True, "last_reported": int(stamp.timestamp()),
                                  "experimental_risk": {"no_bike_30m": 0.8, "no_dock_30m": 0.1} if modeled else None}]}
        rows = [frame(0, 1, "a", True), frame(30, 0, "a", True),
                frame(0, 2, "b", False), frame(30, 0, "b", False)]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "snapshots.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with patch.object(analytics, "SOURCE", source), patch.object(analytics, "DAILY", Path(directory) / "daily"):
                report = analytics.write_day(day)
                self.assertEqual(report["stations"]["a"]["targets"]["no_bike"]["model"]["n"], 1)
                self.assertIsNone(report["stations"]["b"]["targets"]["no_bike"]["model"])
                self.assertEqual(report["stations"]["b"]["targets"]["no_bike"]["persistence"]["n"], 1)
                self.assertEqual(report["stations"]["a"]["counts"]["matched"], 1)
                history = analytics.station_history("a", "month", dt.datetime(2026, 10, 18, 2, tzinfo=dt.timezone.utc))
                self.assertEqual(len(history["availability"]), 1)
                self.assertEqual(len(history["daily_accuracy"]), 1)

    def test_calendar_day_respects_chicago_daylight_saving(self):
        report = analytics.score_day(dt.date(2026, 11, 1))
        start, end = [dt.datetime.fromisoformat(value) for value in report["window_utc"]]
        self.assertEqual((end - start).total_seconds(), 25 * 3600)

    def test_no_report_is_written_before_collection_begins(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "empty.jsonl"
            source.write_text("")
            daily = Path(directory) / "daily"
            with patch.object(analytics, "SOURCE", source), patch.object(analytics, "DAILY", daily):
                self.assertEqual(analytics.write_day(dt.date(2026, 10, 15))["polls"], 0)
                self.assertFalse(daily.exists())


if __name__ == "__main__":
    unittest.main()

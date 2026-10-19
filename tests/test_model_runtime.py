import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_runtime
from model_runtime import ModelRuntime

UTC = dt.timezone.utc
BENSON = "a3b2e20c-a135-11e9-9cda-0a87ae2ba916"   # in v1 and in the 2022 model
NEW = "2246085102741633664"                         # Noyes CTA: v1 only


def station(sid, name, bikes, docks, fresh=True):
    return {"id": sid, "name": name, "bikes": bikes, "docks": docks, "renting": True, "returning": True,
            "fresh": fresh, "last_reported": 0}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.rt = ModelRuntime(fetch_weather=False)
        self.now = dt.datetime(2026, 10, 19, 17, 0, tzinfo=UTC)

    def test_champion_is_v1_and_every_model_is_computed(self):
        self.assertEqual(self.rt.champion, "trip_flow_v1")
        out = self.rt.predict_all(station(BENSON, "Benson Ave & Church St", 3, 16), self.now)
        self.assertEqual(set(out), {"trip_flow_v1", "model_2022", "trip_flow_v2", "trip_flow_v3", "trip_flow_v4"})
        for name, risk in out.items():
            self.assertIsNotNone(risk, name)
            for v in risk.values():
                self.assertTrue(0 <= v <= 1)

    def test_2022_model_only_for_its_six_stations(self):
        out = self.rt.predict_all(station(NEW, "Noyes CTA", 2, 13), self.now)
        self.assertIsNone(out["model_2022"])
        self.assertIsNotNone(out["trip_flow_v1"])

    def test_stale_station_gets_no_estimates(self):
        out = self.rt.predict_all(station(BENSON, "Benson Ave & Church St", 3, 16, fresh=False), self.now)
        self.assertTrue(all(v is None for v in out.values()))

    def test_one_failing_model_does_not_block_others(self):
        with patch("trip_flow_v4.predict_v4", side_effect=RuntimeError("boom")):
            out = self.rt.predict_all(station(BENSON, "Benson Ave & Church St", 3, 16), self.now)
        self.assertIsNone(out["trip_flow_v4"])
        self.assertIsNotNone(out["trip_flow_v1"])

    def test_registry_override_switches_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "override.json"
            override.write_text(json.dumps({"champion": "trip_flow_v4"}))
            with patch.object(model_runtime, "OVERRIDE", override):
                self.assertEqual(ModelRuntime(fetch_weather=False).champion, "trip_flow_v4")
            override.write_text(json.dumps({"champion": "not_a_model"}))
            with patch.object(model_runtime, "OVERRIDE", override):
                self.assertEqual(ModelRuntime(fetch_weather=False).champion, "trip_flow_v1")

    def test_history_seed_and_observe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.jsonl"
            now = dt.datetime.now(UTC)
            lines = []
            for m in range(30):
                stamp = now - dt.timedelta(minutes=30 - m)
                lines.append(json.dumps({"collected_at": stamp.isoformat(), "stations": [
                    {"station_id": BENSON, "bikes": 0, "docks": 19, "renting": True, "returning": True,
                     "last_reported": stamp.timestamp() - 10}]}))
            path.write_text("\n".join(lines) + "\n")
            self.assertEqual(self.rt.load_history(path), 30)
            self.assertEqual(len(self.rt.history[BENSON]), 30)


if __name__ == "__main__":
    unittest.main()

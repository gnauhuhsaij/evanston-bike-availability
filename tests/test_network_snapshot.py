import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import network_reader
import server


def feeds(bikes=3):
    info = {"data": {"stations": [
        {"station_id": "evan1", "name": "Benson Ave & Church St", "short_name": "CHI00737", "lat": 42.04, "lon": -87.68, "capacity": 19, "region_id": "35"},
        {"station_id": "chi1", "name": "Clark St & Lake St", "short_name": "CHI00001", "lat": 41.88, "lon": -87.63, "capacity": 23, "region_id": "1"},
    ]}}
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    status = {"last_updated": int(now), "data": {"stations": [
        {"station_id": "evan1", "num_bikes_available": bikes, "num_docks_available": 16 - bikes, "num_ebikes_available": 1,
         "num_bikes_disabled": 0, "num_docks_disabled": 0, "is_renting": 1, "is_returning": 1, "last_reported": int(now) - 30},
        {"station_id": "chi1", "num_bikes_available": 7, "num_docks_available": 16, "num_ebikes_available": 2,
         "num_bikes_disabled": 1, "num_docks_disabled": 0, "is_renting": 1, "is_returning": 1, "last_reported": int(now) - 40},
    ]}}
    return info, status


class NetworkSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(server, "SNAPSHOTS", root / "evanston.jsonl"),
                        patch.object(server, "NETWORK_DIR", root / "network"),
                        patch.object(server, "network_written", {})]
        for p in self.patches:
            p.start()
        self.root = root

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_polls(self, n=2):
        for i in range(n):
            info, status = feeds(bikes=3 + i)
            with patch.object(server, "fetch_json", side_effect=[info, status]):
                server.refresh()

    def test_evanston_file_keeps_its_format_and_only_evanston(self):
        self.run_polls(2)
        frames = [json.loads(l) for l in (self.root / "evanston.jsonl").read_text().splitlines()]
        self.assertEqual(len(frames), 2)
        self.assertEqual(set(frames[0]), {"collected_at", "source_updated_at", "model_version", "stations"})
        self.assertEqual([s["station_id"] for s in frames[0]["stations"]], ["evan1"])

    def test_network_file_has_all_stations_and_one_metadata_line(self):
        self.run_polls(3)
        day = dt.datetime.now(server.LOCAL).date().isoformat()
        path = self.root / "network" / f"{day}.jsonl.gz"
        with gzip.open(path, "rt") as handle:
            kinds = [json.loads(l)["type"] for l in handle]
        self.assertEqual(kinds, ["stations", "poll", "poll", "poll"])
        polls = list(network_reader.polls(day, self.root / "network"))
        self.assertEqual(len(polls), 3)
        self.assertEqual(polls[-1][1]["chi1"]["ebikes"], 2)
        self.assertEqual(polls[-1][1]["evan1"]["bikes"], 5)

    def test_network_failure_does_not_stop_evanston_collection(self):
        info, status = feeds()
        with patch.object(server, "fetch_json", side_effect=[info, status]), \
             patch.object(server, "write_network_snapshot", side_effect=OSError("disk full")):
            server.refresh()
        self.assertEqual(len((self.root / "evanston.jsonl").read_text().splitlines()), 1)
        self.assertIn("disk full", server.state["network_last_error"])

    def test_reader_skips_truncated_tail(self):
        self.run_polls(2)
        day = dt.datetime.now(server.LOCAL).date().isoformat()
        path = self.root / "network" / f"{day}.jsonl.gz"
        path.write_bytes(path.read_bytes() + gzip.compress(b'{"type":"poll"')[:-6])
        self.assertEqual(len(list(network_reader.polls(day, self.root / "network"))), 2)


if __name__ == "__main__":
    unittest.main()

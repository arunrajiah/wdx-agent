import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wdx_agent as agent  # noqa: E402


def settings(tmp: str, source: str, path: str, extra: str = "") -> agent.Settings:
    ini = Path(tmp) / f"{source}.ini"
    ini.write_text(
        f"[agent]\nendpoint = http://localhost:9/api/v1/events\napi_key = test\nsource = {source}\n"
        f"path = {path}\nstation_id = station1\nstate_file = {tmp}/state.json\n{extra}"
    )
    return agent.Settings(str(ini))


class BirdnetPi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "birds.db")
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE detections (Date DATE, Time TIME, Sci_Name TEXT, Com_Name TEXT, Confidence FLOAT, Lat FLOAT, "
            "Lon FLOAT, Cutoff FLOAT, Week INT, Sens FLOAT, Overlap FLOAT, File_Name TEXT)"
        )
        con.executemany(
            "INSERT INTO detections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("2026-10-01", "06:12:03", "Copsychus saularis", "Oriental Magpie-Robin", 0.91, 13.08274, 80.27071, 0.7, 39, 1, 0, "a.mp3"),
                ("2026-10-01", "06:15:00", "Acridotheres tristis", "Common Myna", 0.42, 13.08274, 80.27071, 0.7, 39, 1, 0, "b.mp3"),
            ],
        )
        con.commit()
        con.close()

    def test_maps_rows_and_filters_low_confidence(self):
        s = settings(self.tmp, "birdnet-pi", self.db)
        out = list(agent.read_birdnet_pi(s, None))
        self.assertEqual(len(out), 1)
        cursor, ev = out[0]
        self.assertEqual(cursor, 1)
        self.assertEqual(ev["wdx"], "0.1")
        self.assertEqual(ev["eventId"], "birdnet-pi:station1:1")
        self.assertEqual(ev["detection"]["scientificName"], "Copsychus saularis")
        self.assertEqual(ev["deployment"]["sensorType"], "acoustic-recorder")
        self.assertRegex(ev["eventStart"], r"^2026-10-01T06:12:03[+-]\d\d:\d\d$")

    def test_coordinates_are_rounded_before_leaving_the_device(self):
        s = settings(self.tmp, "birdnet-pi", self.db, "round_coords = 1\n")
        _, ev = next(agent.read_birdnet_pi(s, None))
        self.assertEqual(ev["deployment"]["latitude"], 13.1)
        self.assertEqual(ev["deployment"]["longitude"], 80.3)
        self.assertEqual(ev["deployment"]["coordinateUncertaintyMeters"], 11100)

    def test_cursor_skips_rows_already_sent(self):
        s = settings(self.tmp, "birdnet-pi", self.db)
        self.assertEqual(list(agent.read_birdnet_pi(s, 1)), [])


class BirdnetGo(unittest.TestCase):
    def test_falls_back_to_config_coordinates(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "birdnet.db")
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, date TEXT, time TEXT, scientific_name TEXT, common_name TEXT, "
            "confidence REAL, latitude REAL, longitude REAL, clip_name TEXT)"
        )
        con.execute(
            "INSERT INTO notes (date,time,scientific_name,common_name,confidence,latitude,longitude,clip_name) "
            "VALUES ('2026-10-01','07:01:10','Psittacula krameri','Rose-ringed Parakeet',0.88,0,0,'clips/x.wav')"
        )
        con.commit()
        con.close()
        s = settings(tmp, "birdnet-go", db, "latitude = 12.9716\nlongitude = 77.5946\n")
        _, ev = next(agent.read_birdnet_go(s, None))
        self.assertEqual((ev["deployment"]["latitude"], ev["deployment"]["longitude"]), (12.97, 77.59))
        self.assertEqual(ev["media"], {"mediaType": "audio", "fileName": "x.wav"})


class SpeciesNet(unittest.TestCase):
    def test_skips_blank_and_low_confidence(self):
        tmp = tempfile.mkdtemp()
        cam = Path(tmp) / "cam"
        cam.mkdir()
        (cam / "predictions.json").write_text(json.dumps({"predictions": [
            {"filepath": "/x/1.JPG", "prediction": "u;mammalia;carnivora;felidae;panthera;pardus;leopard", "prediction_score": 0.93},
            {"filepath": "/x/2.JPG", "prediction": "u;;;;;;blank", "prediction_score": 0.99},
            {"filepath": "/x/3.JPG", "prediction": "u;mammalia;cetartiodactyla;cervidae;axis;axis;chital", "prediction_score": 0.55},
        ]}))
        s = settings(tmp, "speciesnet", str(cam), "latitude = 11.41\nlongitude = 76.69\n")
        out = list(agent.read_speciesnet(s, None))
        self.assertEqual(len(out), 1)
        cursor, ev = out[0]
        self.assertEqual(ev["detection"]["scientificName"], "Panthera pardus")
        self.assertEqual(ev["deployment"]["sensorType"], "camera-trap")
        self.assertEqual(list(agent.read_speciesnet(s, cursor)), [])


class Ndjson(unittest.TestCase):
    def test_tails_complete_lines_only(self):
        tmp = tempfile.mkdtemp()
        f = Path(tmp) / "events.wdx.ndjson"
        f.write_text('{"eventId":"a"}\n{"eventId":"b"}\n{"eventId":"partial"')
        s = settings(tmp, "ndjson", str(f))
        out = list(agent.read_ndjson(s, None))
        self.assertEqual([e["eventId"] for _, e in out], ["a", "b"])
        self.assertEqual(list(agent.read_ndjson(s, out[-1][0])), [])


if __name__ == "__main__":
    unittest.main()

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


class BirdNetGoV2(unittest.TestCase):
    def test_reads_detections_table(self):
        tmp = tempfile.mkdtemp()
        db = f"{tmp}/birdnet.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE ai_models (id INTEGER PRIMARY KEY, name TEXT, version TEXT);"
            "CREATE TABLE label_types (id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE labels (id INTEGER PRIMARY KEY, scientific_name TEXT, model_id INTEGER, label_type_id INTEGER);"
            "CREATE TABLE detections (id INTEGER PRIMARY KEY, model_id INTEGER, label_id INTEGER, detected_at INTEGER, confidence REAL,"
            " latitude REAL, longitude REAL, clip_name TEXT, unlikely NUMERIC DEFAULT 0, legacy_id INTEGER);"
            "INSERT INTO ai_models VALUES (1, 'BirdNET', '2.4');"
            "INSERT INTO label_types VALUES (1, 'species'), (2, 'noise');"
            "INSERT INTO labels VALUES (1, 'Turdus migratorius', 1, 1), (2, 'Engine', 1, 2);"
            "INSERT INTO detections VALUES (1, 1, 1, 1791392364, 0.9, 42.36, -71.06, 'clips/a.wav', 0, 77),"
            " (2, 1, 2, 1791392400, 0.95, 42.36, -71.06, NULL, 0, NULL),"
            " (3, 1, 1, 1791392500, 0.9, 42.36, -71.06, NULL, 1, NULL),"
            " (4, 1, 1, 1791392600, 0.92, 42.36, -71.06, NULL, 0, NULL);"
        )
        con.close()
        s = settings(tmp, "birdnet-go", db, "latitude = 42.36\nlongitude = -71.06\n")
        out = list(agent.read_birdnet_go(s, None))
        self.assertEqual([c for c, _ in out], [{"d": 1}, {"d": 2}, {"d": 3}, {"d": 4}])
        events = [e for _, e in out if e]
        self.assertEqual([e["eventId"] for e in events], ["birdnet-go:station1:77", "birdnet-go:station1:d4"])
        self.assertEqual(events[0]["eventStart"], "2026-10-07T16:59:24Z")
        self.assertEqual(events[0]["detection"]["classifier"], {"name": "BirdNET", "version": "2.4"})
        self.assertEqual(list(agent.read_birdnet_go(s, {"d": 4})), [])


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


class BatDetect2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = Path(self.tmp) / "results"
        self.out.mkdir()
        call = lambda cls, p, t: {"class": cls, "class_prob": p, "det_prob": 0.9, "start_time": t, "end_time": t + 0.005,
                                  "low_freq": 40000, "high_freq": 80000, "event": "Echolocation", "individual": "-1"}
        (self.out / "20260930_213000.WAV.json").write_text(json.dumps({
            "id": "20260930_213000.WAV", "duration": 5.0, "time_exp": 1, "annotated": False, "class_name": "Pipistrellus pipistrellus",
            "annotation": [call("Pipistrellus pipistrellus", 0.81, 1.20), call("Pipistrellus pipistrellus", 0.93, 1.31),
                           call("Pipistrellus pipistrellus", 0.40, 1.42),   # below min_confidence
                           call("Myotis daubentonii", 0.88, 3.00),          # a single call: below min_calls
                           call("Nyctalus noctula", 0.75, 0.50), call("Nyctalus noctula", 0.79, 0.62)],
        }))
        (self.out / "notes.json").write_text(json.dumps({"something": "else"}))

    def test_groups_calls_into_one_event_per_species(self):
        s = settings(self.tmp, "batdetect2", str(self.out), "latitude = 51.5072\nlongitude = -0.1276\n")
        out = list(agent.read_batdetect2(s, None))
        names = [ev["detection"]["scientificName"] for _, ev in out]
        self.assertEqual(names, ["Nyctalus noctula", "Pipistrellus pipistrellus"])
        pip = out[1][1]
        self.assertEqual(pip["detection"]["confidence"], 0.93)
        self.assertEqual(pip["detection"]["classifier"]["name"], "BatDetect2")
        self.assertEqual(pip["source"]["system"], "batdetect2")
        self.assertEqual(pip["media"], {"mediaType": "audio", "fileName": "20260930_213000.WAV"})
        # recording start comes from the file name (UTC), plus the offset of the first call
        self.assertEqual(pip["eventStart"], "2026-09-30T21:30:01+00:00")
        self.assertEqual((pip["deployment"]["latitude"], pip["deployment"]["longitude"]), (51.51, -0.13))

    def test_does_not_resend_a_file(self):
        s = settings(self.tmp, "batdetect2", str(self.out), "latitude = 51.5\nlongitude = -0.1\n")
        cursor = list(agent.read_batdetect2(s, None))[-1][0]
        self.assertEqual(list(agent.read_batdetect2(s, cursor)), [])


class CsvSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Path(self.tmp) / "id.csv"
        self.f.write_text(
            "IN FILE,DATE,TIME,AUTO ID,MATCH RATIO\n"
            "a.wav,2026-09-30,21:30:05,EPTFUS,0.92\n"
            "b.wav,2026-09-30,21:31:10,NoID,0.00\n"
            "c.wav,2026-09-30,21:35:40,MYOLUC,0.40\n"
            "d.wav,not a date,xx,EPTFUS,0.9\n"
            "e.wav,2026-09-30,22:02:00,LASBOR,0.85\n")
        (Path(self.tmp) / "codes.csv").write_text("EPTFUS,Eptesicus fuscus\nMYOLUC,Myotis lucifugus\nLASBOR,Lasiurus borealis\n")
        self.extra = ("latitude = 40.0\nlongitude = -75.0\nclassifier = Kaleidoscope Pro\nsensor_model = Song Meter Mini Bat\n"
                      "csv_species = AUTO ID\ncsv_confidence = MATCH RATIO\ncsv_date = DATE\ncsv_time = TIME\n"
                      "csv_datetime_format = %Y-%m-%d %H:%M:%S\ncsv_file = IN FILE\n"
                      f"csv_species_map = {self.tmp}/codes.csv\n")

    def test_maps_columns_and_codes_and_skips_bad_rows(self):
        s = settings(self.tmp, "csv", str(self.f), self.extra)
        out = list(agent.read_csv(s, None))
        self.assertEqual(len(out), 5)                      # every row moves the cursor
        events = [ev for _, ev in out if ev]
        self.assertEqual([e["detection"]["scientificName"] for e in events], ["Eptesicus fuscus", "Lasiurus borealis"])
        e = events[0]
        self.assertEqual(e["source"]["system"], "other")
        self.assertEqual(e["detection"]["classifier"]["name"], "Kaleidoscope Pro")
        self.assertEqual(e["deployment"]["sensorModel"], "Song Meter Mini Bat")
        self.assertEqual(e["media"]["fileName"], "a.wav")
        self.assertRegex(e["eventStart"], r"^2026-09-30T21:30:05[+-]\d\d:\d\d$")

    def test_cursor_resumes_after_appended_rows(self):
        s = settings(self.tmp, "csv", str(self.f), self.extra)
        cursor = list(agent.read_csv(s, None))[-1][0]
        self.assertEqual(list(agent.read_csv(s, cursor)), [])
        with open(self.f, "a") as fh:
            fh.write("f.wav,2026-09-30,23:00:00,EPTFUS,0.99\n")
        more = [ev for _, ev in agent.read_csv(s, cursor) if ev]
        self.assertEqual(len(more), 1)

    def test_confidence_scale(self):
        f = Path(self.tmp) / "pct.csv"
        f.write_text("species,when,score\nPipistrellus pipistrellus,2026-09-30T21:30:05+01:00,87\n")
        s = settings(self.tmp, "csv", str(f), "latitude = 51\nlongitude = 0\ncsv_species = species\ncsv_datetime = when\ncsv_confidence = score\ncsv_confidence_scale = 100\n")
        ev = [ev for _, ev in agent.read_csv(s, None) if ev][0]
        self.assertEqual(ev["detection"]["confidence"], 0.87)
        self.assertEqual(ev["eventStart"], "2026-09-30T21:30:05+01:00")


if __name__ == "__main__":
    unittest.main()

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
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


class BirdNetGoLayouts(unittest.TestCase):
    def test_layout_detection(self):
        self.assertEqual(agent.birdnet_go_layout({"detections", "labels", "ai_models", "label_types"}), "detections")
        self.assertEqual(agent.birdnet_go_layout({"detections", "labels"}), "detections")
        self.assertEqual(agent.birdnet_go_layout({"notes", "results"}), "notes")
        self.assertIsNone(agent.birdnet_go_layout({"detections"}))   # no labels: not the current layout
        self.assertIsNone(agent.birdnet_go_layout(set()))

    def test_unknown_layout_warns_once_and_sends_nothing(self):
        tmp = tempfile.mkdtemp()
        db = f"{tmp}/birdnet.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY)")
        con.close()
        s = settings(tmp, "birdnet-go", db, "latitude = 1\nlongitude = 2\n")
        logged = []
        orig = agent.log
        agent.log = logged.append
        try:
            self.assertEqual(list(agent.read_birdnet_go(s, None)), [])
            self.assertEqual(list(agent.read_birdnet_go(s, None)), [])
        finally:
            agent.log = orig
        self.assertEqual(len(logged), 1)
        self.assertIn("layout not recognised", logged[0])
        self.assertIn("observations", logged[0])


@contextmanager
def local_zone(zone: str):
    """Run with this host time zone, as after `timedatectl set-timezone` and an agent restart."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def instant(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


# A detection on a Base in India: 2026-11-20 10:51:34 UTC = 16:21:34 IST (seen in production).
UTC_INSTANT = datetime(2026, 11, 20, 10, 51, 34, tzinfo=timezone.utc)


def notes_db(tmp: str, rows) -> str:
    db = f"{tmp}/birdnet.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, date TEXT, time TEXT, scientific_name TEXT, common_name TEXT, "
                "confidence REAL, latitude REAL, longitude REAL, clip_name TEXT)")
    con.executemany("INSERT INTO notes (id,date,time,scientific_name,common_name,confidence,latitude,longitude,clip_name) "
                    "VALUES (?,?,?,'Psittacula krameri','Rose-ringed Parakeet',0.9,13.08,80.27,NULL)", rows)
    con.commit()
    con.close()
    return db


class TimeZoneChange(unittest.TestCase):
    """Event times stay true UTC instants when the Base moves from Etc/UTC to its local zone (Asia/Kolkata)."""

    def test_detections_layout_is_the_same_instant_in_any_zone(self):
        tmp = tempfile.mkdtemp()
        db = f"{tmp}/birdnet.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE ai_models (id INTEGER PRIMARY KEY, name TEXT, version TEXT);"
            "CREATE TABLE labels (id INTEGER PRIMARY KEY, scientific_name TEXT, model_id INTEGER, label_type_id INTEGER);"
            "CREATE TABLE detections (id INTEGER PRIMARY KEY, model_id INTEGER, label_id INTEGER, detected_at INTEGER, confidence REAL,"
            " latitude REAL, longitude REAL, clip_name TEXT, unlikely NUMERIC DEFAULT 0, legacy_id INTEGER);"
            "INSERT INTO ai_models VALUES (1, 'BirdNET', '2.4');"
            "INSERT INTO labels VALUES (1, 'Psittacula krameri', 1, 1);"
            f"INSERT INTO detections VALUES (1, 1, 1, {int(UTC_INSTANT.timestamp())}, 0.9, 13.08, 80.27,"
            " 'clips/Psittacula_krameri_90p_20261120T162134Z.wav', 0, NULL);"
        )
        con.close()
        s = settings(tmp, "birdnet-go", db)
        seen = []
        for zone in ("Etc/UTC", "Asia/Kolkata", "America/New_York"):
            with local_zone(zone):
                _, ev = next(agent.read_birdnet_go(s, None))
                seen.append(ev["eventStart"])
        self.assertEqual(seen, ["2026-11-20T10:51:34Z"] * 3)   # detected_at is Unix seconds: no shift

    def test_notes_layout_reads_wall_time_in_the_local_zone(self):
        tmp = tempfile.mkdtemp()
        with local_zone("Etc/UTC"):   # before: host and BirdNET-Go both on UTC
            db = notes_db(tmp, [(1, "2026-11-20", "10:51:34")])
            _, ev = next(agent.read_birdnet_go(settings(tmp, "birdnet-go", db), None))
            self.assertEqual(instant(ev["eventStart"]), UTC_INSTANT)
        tmp = tempfile.mkdtemp()
        with local_zone("Asia/Kolkata"):   # after: both on IST, BirdNET-Go writes 16:21:34
            db = notes_db(tmp, [(1, "2026-11-20", "16:21:34")])
            _, ev = next(agent.read_birdnet_go(settings(tmp, "birdnet-go", db), None))
            self.assertEqual(ev["eventStart"], "2026-11-20T16:21:34+05:30")
            self.assertEqual(instant(ev["eventStart"]), UTC_INSTANT)

    def test_notes_written_before_the_change_keep_their_zone(self):
        tmp = tempfile.mkdtemp()
        # Row 1 was written while BirdNET-Go ran on UTC and not sent yet; row 2 ten minutes later, on IST.
        db = notes_db(tmp, [(1, "2026-11-20", "10:51:34"), (2, "2026-11-20", "16:31:34")])
        with local_zone("Asia/Kolkata"):
            plain = [ev["eventStart"] for _, ev in agent.read_birdnet_go(settings(tmp, "birdnet-go", db), None)]
            self.assertEqual(instant(plain[0]), datetime(2026, 11, 20, 5, 21, 34, tzinfo=timezone.utc))  # 5.5 h early
            s = settings(tmp, "birdnet-go", db, "notes_zone_before = 1 Etc/UTC\n")
            out = list(agent.read_birdnet_go(s, None))
        self.assertEqual([c for c, _ in out], [1, 2])   # same cursor: nothing sent twice or skipped
        self.assertEqual([instant(e["eventStart"]) for _, e in out],
                         [UTC_INSTANT, datetime(2026, 11, 20, 11, 1, 34, tzinfo=timezone.utc)])

    def test_zone_boundaries_parse(self):
        b = agent.parse_zone_boundaries("300 Asia/Kolkata, 120 Etc/UTC, x y, 5 Not/AZone")
        self.assertEqual([(i, str(z)) for i, z in b], [(120, "Etc/UTC"), (300, "Asia/Kolkata")])
        self.assertEqual(agent.parse_zone_boundaries(""), [])


class TestStation(unittest.TestCase):
    def test_test_flag_only_when_on(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "birdnet.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, date TEXT, time TEXT, scientific_name TEXT, common_name TEXT, "
                    "confidence REAL, latitude REAL, longitude REAL, clip_name TEXT)")
        con.execute("INSERT INTO notes (date,time,scientific_name,common_name,confidence,latitude,longitude,clip_name) "
                    "VALUES ('2026-10-01','07:01:10','Psittacula krameri','Rose-ringed Parakeet',0.9,1,2,NULL)")
        con.commit()
        con.close()
        _, ev = next(agent.read_birdnet_go(settings(tmp, "birdnet-go", db), None))
        self.assertNotIn("test", ev)
        _, ev = next(agent.read_birdnet_go(settings(tmp, "birdnet-go", db, "test = yes\n"), None))
        self.assertIs(ev["test"], True)


class ProgressAndRecovery(unittest.TestCase):
    def test_hourly_summary(self):
        now = [0.0]
        p = agent.Progress(clock=lambda: now[0])
        logged = []
        orig, agent.log = agent.log, logged.append
        try:
            p.events({"received": 5, "inserted": 4, "rejected": 1})
            p.status_sent()
            self.assertIsNone(p.ok())                 # not an hour yet
            now[0] = 3600
            line = p.ok()
            self.assertIn("5 events sent (4 new, 1 rejected) and 1 status reports in the last 60 min", line)
            self.assertIsNone(p.ok())                 # counters start again
        finally:
            agent.log = orig
        self.assertEqual(len(logged), 1)

    def test_summary_right_after_recovery(self):
        now = [100.0]
        p = agent.Progress(clock=lambda: now[0])
        orig, agent.log = agent.log, lambda m: None
        try:
            p.failed()
            p.failed()
            now[0] = 400
            p.events({"received": 3, "inserted": 3, "rejected": 0})
            line = p.ok()
        finally:
            agent.log = orig
        self.assertTrue(line.startswith("connection back after 2 failed attempt(s); summary: 3 events sent"))

    def test_network_errors_retry_within_a_minute(self):
        import urllib.error
        net = urllib.error.URLError("Network is unreachable")
        self.assertEqual(agent.retry_wait(net, 3600, 60), 60)
        self.assertEqual(agent.retry_wait(TimeoutError(), 960, 30), 30)
        http = urllib.error.HTTPError("http://x", 503, "busy", {}, None)
        self.assertEqual(agent.retry_wait(http, 960, 60), 1920)
        self.assertEqual(agent.retry_wait(http, 3600, 60), 3600)

    def test_service_waits_for_the_network(self):
        unit = (Path(__file__).resolve().parent.parent / "wdx-agent.service").read_text()
        self.assertIn("After=network-online.target", unit)
        self.assertIn("Wants=network-online.target", unit)


class StatusAndPickup(unittest.TestCase):
    def test_status_record_and_cursor_helpers(self):
        tmp = tempfile.mkdtemp()
        db = f"{tmp}/birds.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, date TEXT, time TEXT, scientific_name TEXT, common_name TEXT, "
                    "confidence REAL, latitude REAL, longitude REAL, clip_name TEXT)")
        for i in range(3):
            con.execute("INSERT INTO notes (date,time,scientific_name,common_name,confidence,latitude,longitude,clip_name) "
                        "VALUES ('2026-10-01','07:01:10','Psittacula krameri','Rose-ringed Parakeet',0.9,0,0,NULL)")
        con.commit()
        con.close()
        s = settings(tmp, "birdnet-go", db, "latitude = 12.97\nlongitude = 77.59\ndevice_id = wnb_test\n"
                     "battery_command = echo '{\"percent\": 81, \"volts\": 13.1, \"other\": 1}'\n")
        st = agent.collect_status(s)
        self.assertEqual((st["kind"], st["deviceId"], st["queue"]["pending"]), ("device-status", "wnb_test", 3))
        self.assertEqual(st["battery"], {"percent": 81, "volts": 13.1})
        self.assertTrue(s.status_endpoint.endswith("/api/v1/devices/status"))
        batch = agent.pending_batch(s)
        agent.save_cursor(s, batch[1][0])
        self.assertEqual(len(agent.pending_batch(s)), 1)


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



class CameraLabels(unittest.TestCase):
    def test_label_forms(self):
        f = agent.taxon_from_label
        self.assertEqual(f("Cyanocitta cristata", {}), ("Cyanocitta cristata", ""))
        self.assertEqual(f("Blue Jay (Cyanocitta cristata)", {}), ("Cyanocitta cristata", "Blue Jay"))
        self.assertEqual(f("Vulpes vulpes (Red Fox)", {}), ("Vulpes vulpes", "Red Fox"))
        self.assertEqual(f("x;mammalia;carnivora;canidae;vulpes;vulpes;red fox", {}), ("Vulpes vulpes", "red fox"))
        self.assertEqual(f("deer", {"deer": "Capreolus capreolus"}), ("Capreolus capreolus", "deer"))
        for bad in ("person", "car", "bird", "Deer", "", "blank"):
            self.assertIsNone(f(bad, {}), bad)


class MegaDetector(unittest.TestCase):
    def test_classified_images_only(self):
        tmp = tempfile.mkdtemp()
        folder = Path(tmp) / "md"
        folder.mkdir()
        (folder / "out.json").write_text(json.dumps({
            "detection_categories": {"1": "animal", "2": "person"},
            "classification_categories": {"0": "Vulpes vulpes", "1": "Meles meles", "2": "empty"},
            "images": [
                {"file": "a/IMG_0001.JPG", "datetime": "2026:05:01 21:10:00", "detections": [
                    {"category": "1", "conf": 0.95, "bbox": [0.1, 0.2, 0.3, 0.4], "classifications": [["0", 0.91], ["1", 0.05]]},
                    {"category": "1", "conf": 0.90, "classifications": [["0", 0.80]]}]},
                {"file": "a/IMG_0002.JPG", "datetime": "2026:05:01 21:12:00", "detections": [
                    {"category": "1", "conf": 0.10, "classifications": [["1", 0.99]]}]},
                {"file": "a/IMG_0003.JPG", "detections": [{"category": "2", "conf": 0.99}]},
            ]}))
        s = settings(tmp, "megadetector", str(folder), "latitude = 51.5\nlongitude = -0.1\n")
        evs = [e for _, e in agent.SOURCES["megadetector"](s, None) if e]
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual(e["detection"]["scientificName"], "Vulpes vulpes")
        self.assertAlmostEqual(e["detection"]["confidence"], 0.91)
        self.assertEqual(e["media"]["region"]["coordinates"], [0.1, 0.2, 0.3, 0.4])
        self.assertTrue(e["eventStart"].startswith("2026-05-01T21:10:00"))
        self.assertEqual(e["deployment"]["sensorType"], "camera-trap")


class Frigate(unittest.TestCase):
    def test_events(self):
        tmp = tempfile.mkdtemp()
        s = settings(tmp, "frigate", "http://frigate:5000", "latitude = 40.7\nlongitude = -74.0\nspecies_map = " + tmp + "/map.csv\n")
        Path(tmp, "map.csv").write_text("deer,Odocoileus virginianus\n")
        Path(tmp, "frigate_bird_labels.txt").write_text("Cyanocitta cristata (Blue Jay)\nRhipidura fuliginosa placabilis (North Island Fantail)\n")
        now = 1790000000.0
        events = [
            {"id": "1", "label": "bird", "sub_label": ["Cardinalis cardinalis", 0.92], "start_time": now + 1, "end_time": now + 5,
             "camera": "feeder", "data": {"top_score": 0.8, "box": [0.1, 0.1, 0.2, 0.2]}},
            {"id": "1b", "label": "bird", "sub_label": "Blue Jay", "start_time": now + 1.5, "end_time": now + 2, "data": {"top_score": 0.95}},
            {"id": "2", "label": "person", "start_time": now + 2, "end_time": now + 3, "data": {"top_score": 0.9}},
            {"id": "3", "label": "deer", "start_time": now + 3, "end_time": now + 9, "data": {"top_score": 0.85}},
            {"id": "4", "label": "deer", "start_time": now + 4, "end_time": None, "data": {"top_score": 0.85}},
            {"id": "5", "label": "deer", "start_time": now + 6, "end_time": now + 7, "data": {"top_score": 0.85}},
        ]
        orig = agent.frigate_get
        agent.frigate_get = lambda s_, path: events if path.startswith("/api/events") else "0.16.0"
        try:
            out = list(agent.SOURCES["frigate"](s, now))
        finally:
            agent.frigate_get = orig
        self.assertEqual([c for c, _ in out], [now + 1, now + 1.5, now + 2, now + 3])  # stops at the event still in progress
        sent = [e for _, e in out if e]
        self.assertEqual([e["detection"]["scientificName"] for e in sent], ["Cardinalis cardinalis", "Cyanocitta cristata", "Odocoileus virginianus"])
        self.assertEqual(sent[1]["detection"]["vernacularName"], "Blue Jay")
        self.assertAlmostEqual(sent[1]["detection"]["confidence"], 0.95)
        self.assertAlmostEqual(sent[0]["detection"]["confidence"], 0.92)
        self.assertEqual(sent[0]["detection"]["classifier"]["version"], "0.16.0")
        self.assertEqual(sent[2]["detection"]["vernacularName"], "deer")

if __name__ == "__main__":
    unittest.main()


class Versions(unittest.TestCase):
    def test_newer(self):
        import wdx_agent as w
        self.assertTrue(w.newer("0.4.10", "0.4.9"))
        self.assertTrue(w.newer("0.5.0", "0.4.3"))
        self.assertFalse(w.newer("0.4.3", "0.4.3"))
        self.assertFalse(w.newer("0.4.2", "0.4.3"))

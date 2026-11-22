#!/usr/bin/env python3
"""wdx-agent: push wildlife detections from a Raspberry Pi (or any machine) to a WDX endpoint.

Reads detections that already exist on the device and forwards them as WDX 0.1 events
(https://github.com/arunrajiah/wildlife-detection-exchange). Standard library only: no pip needed.

Sources:
  birdnet-pi   BirdNET-Pi SQLite database (table `detections`)
  birdnet-go   BirdNET-Go SQLite database (table `notes`)
  speciesnet   SpeciesNet / MegaDetector `predictions.json` files in a folder (camera traps)
  batdetect2   BatDetect2 result JSON files in a folder (bat detectors such as AudioMoth)
  csv          any detections table, with the column names given in the config (Kaleidoscope, SonoBat, ...)
  ndjson       any `.wdx.ndjson` file that another tool appends WDX events to

Usage:
  wdx_agent.py --config /etc/wdx-agent.ini            run forever
  wdx_agent.py --config wdx-agent.ini --once          one pass, then exit
  wdx_agent.py --config wdx-agent.ini --dry-run       print events instead of sending
"""
from __future__ import annotations

import argparse
import configparser
import csv
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VERSION = "0.4.8"
BATCH = 500


def log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def local_iso(date: str, clock: str) -> str:
    """Device-local 'YYYY-MM-DD' + 'HH:MM:SS' to ISO 8601 with the local UTC offset for that date."""
    dt = datetime.strptime(f"{date} {clock.split('.')[0]}", "%Y-%m-%d %H:%M:%S")
    return dt.astimezone().isoformat(timespec="seconds")


def parse_zone_boundaries(text: str) -> list:
    """`notes_zone_before`: "<id> <zone>" pairs separated by commas, e.g. "1520 Etc/UTC". Rows of BirdNET-Go's older
    `notes` layout carry wall-clock date and time in the container's time zone; when that zone changes, the rows up
    to <id> were written in <zone>, not in the current local zone. Unreadable pairs are ignored."""
    out = []
    for part in (text or "").split(","):
        bits = part.split()
        if len(bits) == 2 and bits[0].isdigit():
            try:
                out.append((int(bits[0]), ZoneInfo(bits[1])))
            except (ZoneInfoNotFoundError, ValueError):
                log(f"WARNING: notes_zone_before: unknown time zone {bits[1]!r}, ignored")
    return sorted(out, key=lambda b: b[0])


def notes_iso(s, rid: int, date: str, clock: str) -> str:
    """Event time of a BirdNET-Go `notes` row: local wall time, or the zone it was written in (notes_zone_before)."""
    for last, zone in getattr(s, "notes_zones", []):
        if rid <= last:
            dt = datetime.strptime(f"{date} {clock.split('.')[0]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone)
            return dt.isoformat(timespec="seconds")
    return local_iso(date, clock)


class Settings:
    def __init__(self, path: str):
        cp = configparser.ConfigParser(interpolation=None)  # so date formats like %Y-%m-%d can be written plainly
        if not cp.read(path):
            sys.exit(f"config not found: {path}")
        a = cp["agent"]
        self.endpoint = a.get("endpoint", "https://wlnw.org/api/v1/events")
        self.api_key = a.get("api_key", "")
        self.source = a.get("source", "birdnet-pi")
        self.path = os.path.expanduser(a.get("path", ""))
        self.station_id = a.get("station_id", "") or default_station_id()
        self.station_name = a.get("station_name", "")
        self.latitude = a.getfloat("latitude", fallback=None)
        self.longitude = a.getfloat("longitude", fallback=None)
        # Privacy: coordinates are rounded before they leave the device. 2 decimals is about 1 km.
        self.round_coords = a.getint("round_coords", fallback=2)
        self.min_confidence = a.getfloat("min_confidence", fallback=0.7)
        self.interval = a.getint("interval_seconds", fallback=60)
        self.media_base_url = a.get("media_base_url", "")
        self.license = a.get("license", "https://creativecommons.org/licenses/by/4.0/")
        # Test station: every event carries "test": true and the server keeps it off the public map.
        self.test = a.getboolean("test", fallback=False)
        # Detections at or before this time are skipped (cursor still moves): set by the installer when the BirdEcho app
        # already shared this station's detections from a phone, so they are not sent a second time.
        self.start_after = parse_start_after(a.get("start_after", ""))
        self.state_path = os.path.expanduser(a.get("state_file", "~/.wdx-agent-state.json"))
        # What the server sees as source.system. The csv source is generic, so it reports "other" unless told otherwise.
        self.system = a.get("system", "") or ("other" if self.source == "csv" else self.source)
        self.sensor_type = a.get("sensor_type", "acoustic-recorder")
        self.sensor_model = a.get("sensor_model", "")
        self.classifier = a.get("classifier", "")
        self.classifier_version = a.get("classifier_version", "unknown")
        # Recorders such as AudioMoth put the start time in the file name, in UTC unless configured otherwise.
        self.filename_timezone = a.get("filename_timezone", "utc").lower()
        # BirdNET-Go `notes` rows written before a time zone change (set by the Wildlife Network Base).
        self.notes_zones = parse_zone_boundaries(a.get("notes_zone_before", ""))
        self.min_calls = a.getint("min_calls", fallback=2)
        # Camera sources: species_map CSV (label,scientific name), Frigate API token, MegaDetector detection threshold.
        self.species_map = a.get("species_map", "")
        self.api_token = a.get("api_token", "")
        self.frigate_labels_url = a.get("frigate_labels_url", "") or FRIGATE_BIRD_LABELS
        self.min_detection = a.getfloat("min_detection", fallback=0.2)
        self.csv = {k[4:]: v for k, v in a.items() if k.startswith("csv_")}
        # Device health (WDX device-status): sent when device_id is set, from the Wildlife Network device registry.
        self.device_id = a.get("device_id", "")
        self.status_endpoint = a.get("status_endpoint", "") or self.endpoint.rsplit("/events", 1)[0] + "/devices/status"
        self.status_interval = a.getint("status_interval_seconds", fallback=900)
        # Optional command printing JSON such as {"percent": 81, "volts": 13.1, "charging": true} (hardware specific).
        self.battery_command = a.get("battery_command", "")


def parse_start_after(v: str):
    """ISO time (with zone, or UTC when none) to an aware datetime, or None when empty or unreadable."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        log(f"WARNING: start_after {v!r} is not an ISO time; ignored")
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def after_start(s: "Settings", ev: dict) -> bool:
    """False for an event at or before start_after (already shared another way)."""
    if s.start_after is None:
        return True
    try:
        t = datetime.fromisoformat(str(ev.get("eventStart", "")).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)) > s.start_after


def default_station_id() -> str:
    seed = socket.gethostname()
    try:
        seed += Path("/etc/machine-id").read_text().strip()
    except OSError:
        pass
    return hashlib.sha256(seed.encode()).hexdigest()[:12]


def deployment(s: Settings, lat, lon, sensor_type: str, model: str) -> dict | None:
    lat = lat if lat not in (None, 0, 0.0) else s.latitude
    lon = lon if lon not in (None, 0, 0.0) else s.longitude
    if lat is None or lon is None:
        return None
    d = {
        "deploymentId": s.station_id,
        "latitude": round(float(lat), s.round_coords),
        "longitude": round(float(lon), s.round_coords),
        "coordinateUncertaintyMeters": max(1, int(111_000 / (10 ** s.round_coords))),
        "sensorType": sensor_type,
        "sensorModel": model,
    }
    if s.station_name:
        d["name"] = s.station_name
    return d


def base_event(s: Settings, event_id: str, start: str, dep: dict, record_id: str) -> dict:
    return {
        "wdx": "0.1",
        "eventId": event_id,
        "eventStart": start,
        "deployment": dep,
        "review": {"status": "unreviewed"},
        "source": {"system": s.system, "systemVersion": f"wdx-agent/{VERSION}", "sourceRecordId": record_id},
        "license": s.license,
        **({"test": True} if s.test else {}),
    }


# --- sources: each yields (cursor, event) with cursor strictly increasing ---------------------------------

def read_birdnet_pi(s: Settings, cursor):
    db = sqlite3.connect(f"file:{s.path}?mode=ro", uri=True, timeout=10)
    rows = db.execute(
        "SELECT rowid, Date, Time, Sci_Name, Com_Name, Confidence, Lat, Lon, File_Name FROM detections "
        "WHERE rowid > ? AND Confidence >= ? ORDER BY rowid LIMIT ?",
        (int(cursor or 0), s.min_confidence, BATCH),
    ).fetchall()
    db.close()
    for rowid, date, clock, sci, com, conf, lat, lon, fname in rows:
        dep = deployment(s, lat, lon, "acoustic-recorder", "BirdNET-Pi")
        if dep is None:
            sys.exit("no coordinates: set latitude/longitude in the config")
        ev = base_event(s, f"{s.source}:{s.station_id}:{rowid}", local_iso(date, clock), dep, str(rowid))
        ev["detection"] = {"scientificName": sci, "vernacularName": com, "taxonRank": "species",
                           "confidence": float(conf), "classifier": {"name": "BirdNET", "version": "unknown"}}
        ev["media"] = {"mediaType": "audio", "fileName": fname}
        if s.media_base_url:
            ev["media"]["url"] = f"{s.media_base_url.rstrip('/')}/{fname}"
        yield rowid, ev


BIRDNET_GO_WARNED: set = set()


def birdnet_go_layout(tables: set) -> str | None:
    """Which BirdNET-Go datastore this is: "detections" (current: detections + labels, with ai_models and
    label_types when present), "notes" (older releases), or None when neither is recognised."""
    if "detections" in tables and "labels" in tables:
        return "detections"
    if "notes" in tables:
        return "notes"
    return None


def read_birdnet_go(s: Settings, cursor):
    """BirdNET-Go. Older versions keep a `notes` table (cursor = note id). Current versions keep `detections` joined to
    `labels` and `ai_models` (cursor = {"d": detection id}); migrated rows keep their note id as legacy_id, so their
    eventIds match what was sent before the upgrade and the server deduplicates them.
    Times: `detected_at` is Unix seconds, a true UTC instant whatever the time zone. `notes` keeps wall-clock date and
    time in BirdNET-Go's (container's) time zone, read here in this host's zone, so both must be the same zone
    (notes_zone_before covers rows written before a change of zone)."""
    db = sqlite3.connect(f"file:{s.path}?mode=ro", uri=True, timeout=10)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    layout = birdnet_go_layout(tables)
    if layout is None:
        db.close()
        if s.path not in BIRDNET_GO_WARNED:
            BIRDNET_GO_WARNED.add(s.path)
            log(f"WARNING: BirdNET-Go database layout not recognised in {s.path} (tables: "
                f"{', '.join(sorted(tables)) or 'none yet'}); nothing is sent. This agent reads the `detections` + "
                "`labels` layout and the older `notes` layout. If BirdNET-Go was updated, update wdx-agent too.")
        return
    if layout == "detections":
        start = cursor.get("d", 0) if isinstance(cursor, dict) else 0
        rows = db.execute(
            "SELECT d.id, d.legacy_id, d.detected_at, l.scientific_name, d.confidence, d.latitude, d.longitude, d.clip_name, "
            "m.name, m.version, COALESCE(d.unlikely, 0), " + ("lt.name" if "label_types" in tables else "'species'") +
            " FROM detections d JOIN labels l ON l.id = d.label_id LEFT JOIN ai_models m ON m.id = d.model_id " +
            ("LEFT JOIN label_types lt ON lt.id = l.label_type_id " if "label_types" in tables else "") +
            "WHERE d.id > ? ORDER BY d.id LIMIT ?",
            (int(start), BATCH),
        ).fetchall()
        db.close()
        for rid, legacy, at, sci, conf, lat, lon, clip, model, version, unlikely, kind in rows:
            # Rows below the confidence threshold, flagged unlikely for the location, or not a species (noise, human, ...) still move the cursor.
            if conf < s.min_confidence or unlikely or (kind or "species") != "species":
                yield {"d": rid}, None
                continue
            dep = deployment(s, lat, lon, "acoustic-recorder", "BirdNET-Go")
            if dep is None:
                sys.exit("no coordinates: set latitude/longitude in the config")
            record = str(legacy) if legacy else f"d{rid}"
            when = datetime.fromtimestamp(int(at), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            ev = base_event(s, f"{s.source}:{s.station_id}:{record}", when, dep, record)
            ev["detection"] = {"scientificName": sci, "taxonRank": "species", "confidence": float(conf),
                               "classifier": {"name": model or "BirdNET", "version": version or "unknown"}}
            if clip:
                ev["media"] = {"mediaType": "audio", "fileName": os.path.basename(clip)}
            yield {"d": rid}, ev
        return
    rows = db.execute(
        "SELECT id, date, time, scientific_name, common_name, confidence, latitude, longitude, clip_name FROM notes "
        "WHERE id > ? AND confidence >= ? ORDER BY id LIMIT ?",
        (int(cursor or 0), s.min_confidence, BATCH),
    ).fetchall()
    db.close()
    for rid, date, clock, sci, com, conf, lat, lon, clip in rows:
        dep = deployment(s, lat, lon, "acoustic-recorder", "BirdNET-Go")
        if dep is None:
            sys.exit("no coordinates: set latitude/longitude in the config")
        ev = base_event(s, f"{s.source}:{s.station_id}:{rid}", notes_iso(s, rid, date, clock), dep, str(rid))
        ev["detection"] = {"scientificName": sci, "vernacularName": com, "taxonRank": "species",
                           "confidence": float(conf), "classifier": {"name": "BirdNET", "version": "unknown"}}
        if clip:
            ev["media"] = {"mediaType": "audio", "fileName": os.path.basename(clip)}
        yield rid, ev


def read_speciesnet(s: Settings, cursor):
    """Folder of SpeciesNet predictions JSON. Cursor maps each file already sent to its mtime."""
    dep = deployment(s, None, None, "camera-trap", "SpeciesNet")
    if dep is None:
        sys.exit("camera traps need latitude/longitude in the config")
    seen = dict(cursor or {})
    sent = 0
    for f in sorted(Path(s.path).glob("**/*.json")):
        mtime = f.stat().st_mtime
        if seen.get(str(f)) == mtime:
            continue
        try:
            preds = json.loads(f.read_text()).get("predictions", [])
        except (OSError, ValueError):
            continue
        file_events = []
        for p in preds:
            score = p.get("prediction_score")
            label = p.get("prediction", "")
            if score is None or score < s.min_confidence or not label:
                continue
            # SpeciesNet label: uuid;class;order;family;genus;species;common name
            parts = label.split(";")
            genus, species, common = (parts + [""] * 7)[4:7]
            if not genus or common in ("blank", "human", "vehicle"):
                continue
            img = Path(p.get("filepath", ""))
            try:
                start = datetime.fromtimestamp(img.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            except OSError:
                start = datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")
            rid = hashlib.sha256(f"{f}:{p.get('filepath')}".encode()).hexdigest()[:16]
            ev = base_event(s, f"{s.source}:{s.station_id}:{rid}", start, dep, rid)
            det = {"scientificName": f"{genus.capitalize()} {species}".strip(), "taxonRank": "species" if species else "genus",
                   "confidence": float(score), "classifier": {"name": "SpeciesNet", "version": str(p.get("model_version", "unknown"))}}
            if common:
                det["vernacularName"] = common
            ev["detection"] = det
            ev["media"] = {"mediaType": "image", "fileName": img.name or "unknown.jpg"}
            file_events.append(ev)
        seen[str(f)] = mtime
        for ev in file_events:
            sent += 1
            yield dict(seen), ev
        if sent >= BATCH:
            break


FILENAME_TIME = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})[_T-]?(\d{2})[-:]?(\d{2})[-:]?(\d{2})")


def recording_start(s: Settings, name: str, fallback_path: str):
    """Start time of a recording: from its file name (20260930_213000.WAV), else the file's modification time."""
    m = FILENAME_TIME.search(os.path.basename(name))
    if m:
        try:
            dt = datetime(*(int(g) for g in m.groups()))
            return dt.replace(tzinfo=timezone.utc) if s.filename_timezone == "utc" else dt.astimezone()
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(fallback_path)).astimezone()


def read_batdetect2(s: Settings, cursor):
    """Folder of BatDetect2 result JSON files, one per recording. Cursor maps each file already sent to its mtime.

    BatDetect2 reports every echolocation call. A bat pass is many calls, so calls are grouped:
    one event per species per recording, with the highest class probability and at least `min_calls` calls.
    """
    dep = deployment(s, None, None, "acoustic-recorder", s.sensor_model or "Bat detector")
    if dep is None:
        sys.exit("bat detectors need latitude/longitude in the config")
    seen = dict(cursor or {})
    sent = 0
    for f in sorted(Path(s.path).glob("**/*.json")):
        mtime = f.stat().st_mtime
        if seen.get(str(f)) == mtime:
            continue
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        calls = doc.get("annotation") if isinstance(doc, dict) else None
        if not isinstance(calls, list):
            continue  # some other JSON file
        rec = str(doc.get("id") or f.stem)
        start = recording_start(s, rec, str(f))
        by_class: dict = {}
        for c in calls:
            prob, name = c.get("class_prob"), c.get("class")
            if not name or prob is None or prob < s.min_confidence:
                continue
            g = by_class.setdefault(name, {"n": 0, "prob": 0.0, "first": None})
            g["n"] += 1
            g["prob"] = max(g["prob"], float(prob))
            t = c.get("start_time")
            if t is not None and (g["first"] is None or t < g["first"]):
                g["first"] = float(t)
        file_events = []
        for name, g in sorted(by_class.items()):
            if g["n"] < s.min_calls:
                continue
            rid = hashlib.sha256(f"{rec}:{name}".encode()).hexdigest()[:16]
            when = start + timedelta(seconds=g["first"] or 0)
            ev = base_event(s, f"{s.system}:{s.station_id}:{rid}", when.isoformat(timespec="seconds"), dep, rid)
            ev["detection"] = {"scientificName": name, "taxonRank": "species" if " " in name.strip() else "unranked",
                               "confidence": min(1.0, g["prob"]),
                               "classifier": {"name": s.classifier or "BatDetect2", "version": s.classifier_version}}
            ev["media"] = {"mediaType": "audio", "fileName": os.path.basename(rec)}
            file_events.append(ev)
        seen[str(f)] = mtime
        for ev in file_events:
            sent += 1
            yield dict(seen), ev
        if sent >= BATCH:
            break


def read_csv(s: Settings, cursor):
    """Any detections table. Column names come from the config (csv_species, csv_confidence, csv_datetime, ...).

    Cursor is the number of data rows already sent, so the file must only ever be appended to.
    """
    c = s.csv
    if "species" not in c or not ("datetime" in c or ("date" in c and "time" in c)):
        sys.exit("csv source needs csv_species and either csv_datetime or csv_date + csv_time in the config")
    scale = float(c.get("confidence_scale", "1"))
    fmt = c.get("datetime_format", "")
    species_map = {}
    if c.get("species_map"):
        with open(os.path.expanduser(c["species_map"]), newline="", encoding="utf-8-sig") as fh:
            species_map = {r[0].strip(): r[1].strip() for r in csv.reader(fh) if len(r) >= 2}
    done = int(cursor or 0)
    with open(s.path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=c.get("delimiter", ",").replace("\\t", "\t"))
        n = 0
        for i, row in enumerate(reader, start=1):
            if i <= done:
                continue
            if n >= BATCH:
                break
            name = (row.get(c["species"]) or "").strip()
            name = species_map.get(name, name)
            raw_time = (row.get(c["datetime"]) if "datetime" in c else f"{row.get(c['date'], '')} {row.get(c['time'], '')}") or ""
            raw_time = raw_time.strip()
            try:
                conf = float(row[c["confidence"]]) / scale if c.get("confidence") else 1.0
                when = datetime.strptime(raw_time, fmt) if fmt else datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            except (KeyError, ValueError, TypeError):
                n += 1
                yield i, None  # unreadable row: advance past it
                continue
            if when.tzinfo is None:
                # Times without an offset are the device's local time, unless csv_timezone = utc.
                when = when.replace(tzinfo=timezone.utc) if c.get("timezone", "local").lower() == "utc" else when.astimezone()
            n += 1
            if not name or name.lower() in ("noid", "no id", "noise", "none", "unknown") or conf < s.min_confidence:
                yield i, None
                continue
            lat = row.get(c.get("latitude", "")) or None
            lon = row.get(c.get("longitude", "")) or None
            dep = deployment(s, float(lat) if lat else None, float(lon) if lon else None, s.sensor_type, s.sensor_model or "CSV import")
            if dep is None:
                sys.exit("no coordinates: set latitude/longitude in the config, or csv_latitude/csv_longitude columns")
            fname = (row.get(c.get("file", "")) or "").strip()
            rid = hashlib.sha256(f"{raw_time}|{name}|{fname}|{i}".encode()).hexdigest()[:16]
            ev = base_event(s, f"{s.system}:{s.station_id}:{rid}", when.isoformat(timespec="seconds"), dep, rid)
            det = {"scientificName": name, "taxonRank": "species" if " " in name else "unranked", "confidence": max(0.0, min(1.0, conf)),
                   "classifier": {"name": s.classifier or "unknown", "version": s.classifier_version}}
            common = (row.get(c.get("common", "")) or "").strip()
            if common:
                det["vernacularName"] = common
            ev["detection"] = det
            if fname:
                ev["media"] = {"mediaType": "image" if s.sensor_type == "camera-trap" else "audio", "fileName": os.path.basename(fname)}
            yield i, ev


def read_ndjson(s: Settings, cursor):
    """Tail a WDX NDJSON file. Cursor is the byte offset."""
    offset = int(cursor or 0)
    with open(s.path, "rb") as fh:
        if os.path.getsize(s.path) < offset:
            offset = 0  # file was rotated
        fh.seek(offset)
        n = 0
        while n < BATCH:
            line = fh.readline()
            if not line or not line.endswith(b"\n"):
                break
            offset = fh.tell()
            if line.strip():
                try:
                    yield offset, json.loads(line)
                    n += 1
                except ValueError:
                    log(f"skipping malformed line before byte {offset}")


# --- camera sources: a label is only sent when it names a taxon -----------------------------------------

BINOMIAL = re.compile(r"^([A-Z][a-z]+)(?: ([a-z][a-z-]+))?$")
NOT_ANIMALS = {"person", "human", "car", "vehicle", "truck", "bicycle", "motorcycle", "bus", "boat", "blank", "empty",
               "license_plate", "package", "face", "none", "unknown", "animal", "bird", "other"}


def load_species_map(s: Settings) -> dict:
    """Optional two-column CSV (label, scientific name), case-insensitive. Lets a generic label such as 'deer' or a
    common name stand for the species that lives at this site."""
    path = s.species_map
    if not path:
        return {}
    with open(os.path.expanduser(path), newline="", encoding="utf-8-sig") as fh:
        return {r[0].strip().lower(): r[1].strip() for r in csv.reader(fh) if len(r) >= 2 and r[1].strip()}


def taxon_from_label(label: str, species_map: dict):
    """(scientific name, common name) for a classifier label, or None when it does not name a taxon.
    Understands 'Genus species', 'Genus species (Common)', 'Common (Genus species)' and SpeciesNet-style
    'uuid;class;order;family;genus;species;common' strings."""
    raw = (label or "").strip()
    if not raw:
        return None
    mapped = species_map.get(raw.lower())
    if mapped:
        return mapped, raw if not BINOMIAL.match(raw) else ""
    if raw.lower() in NOT_ANIMALS:
        return None
    if ";" in raw:
        parts = (raw.split(";") + [""] * 7)[:7]
        genus, species, common = parts[4].strip(), parts[5].strip(), parts[6].strip()
        if not genus or common.lower() in NOT_ANIMALS:
            return None
        return f"{genus.capitalize()} {species}".strip(), common
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", raw)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        if BINOMIAL.match(a) and " " in a:
            return a, b
        if BINOMIAL.match(b) and " " in b:
            return b, a
    raw = raw.replace("_", " ")
    if " " in raw and BINOMIAL.match(raw[0].upper() + raw[1:]):
        return raw[0].upper() + raw[1:], ""
    return None


def detection(sci: str, common: str, conf: float, name: str, version: str) -> dict:
    d = {"scientificName": sci, "taxonRank": "species" if " " in sci else "genus",
         "confidence": max(0.0, min(1.0, float(conf))), "classifier": {"name": name, "version": version}}
    if common:
        d["vernacularName"] = common
    return d


def frigate_get(s: Settings, path: str):
    headers = {"user-agent": f"wdx-agent/{VERSION}"}
    if s.api_token:
        headers["authorization"] = f"Bearer {s.api_token}"
    req = urllib.request.Request(s.path.rstrip("/") + path, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except ValueError:
        return body.strip()


FRIGATE_BIRD_LABELS = "https://raw.githubusercontent.com/google-coral/test_data/master/inat_bird_labels.txt"


def frigate_bird_map(s: Settings) -> dict:
    """Frigate's bird classification sets the sub label to the common name from this label list
    ('Cyanocitta cristata (Blue Jay)'). Downloaded once next to the state file; common name -> binomial."""
    cache = Path(s.state_path).with_name("frigate_bird_labels.txt")
    if not cache.exists():
        try:
            req = urllib.request.Request(s.frigate_labels_url, headers={"user-agent": f"wdx-agent/{VERSION}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                cache.write_bytes(r.read())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"bird label list not loaded ({e}); only scientific names and species_map are used")
            return {}
    out = {}
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^([A-Z][a-z]+ [a-z-]+)(?: [a-z-]+)? \((.+)\)\s*$", line.strip())
        if m:
            out.setdefault(m.group(2).strip().lower(), m.group(1))
    return out


def read_frigate(s: Settings, cursor):
    """Frigate NVR over its HTTP API (path = http://frigate-host:5000). Uses the bird classification sub label
    (Frigate 0.16+) or a species_map for generic labels such as 'deer'. Cursor = start time of the last finished
    event read; events still in progress are left for the next run so their final label is used."""
    dep = deployment(s, None, None, s.sensor_type if s.sensor_type != "acoustic-recorder" else "camera-trap",
                     s.sensor_model or "Frigate")
    if dep is None:
        sys.exit("cameras need latitude/longitude in the config")
    species_map = {**frigate_bird_map(s), **load_species_map(s)}
    after = float(cursor or 0) or time.time() - 86400  # first run: the last day only
    events = frigate_get(s, f"/api/events?after={after}&limit={BATCH}&sort=date_asc&include_thumbnails=0")
    if not isinstance(events, list):
        return
    version = s.classifier_version
    if version == "unknown":
        try:
            version = str(frigate_get(s, "/api/version"))[:40] or "unknown"
        except (urllib.error.URLError, TimeoutError):
            pass
    for e in sorted(events, key=lambda e: e.get("start_time") or 0):
        start = e.get("start_time")
        if start is None or start <= after:
            continue
        if e.get("end_time") is None:
            return  # still in progress: stop here so it is read once finished
        sub = e.get("sub_label")
        sub_name, sub_score = (sub[0], sub[1]) if isinstance(sub, list) and len(sub) >= 2 else (sub, None)
        data = e.get("data") or {}
        taxon = taxon_from_label(sub_name, species_map) if sub_name else None
        conf = sub_score if taxon and sub_score is not None else (data.get("top_score") or e.get("top_score") or data.get("score") or 0)
        if not taxon:
            taxon = taxon_from_label(e.get("label", ""), species_map)
        if not taxon or e.get("false_positive") or float(conf) < s.min_confidence:
            yield start, None
            continue
        sci, common = taxon
        rid = str(e.get("id"))
        ev = base_event(s, f"{s.system}:{s.station_id}:{rid}",
                        datetime.fromtimestamp(start).astimezone().isoformat(timespec="seconds"), dep, rid)
        ev["detection"] = detection(sci, common, conf, s.classifier or "Frigate", version)
        media = {"mediaType": "image", "fileName": f"{e.get('camera', 'camera')}-{rid}.jpg"}
        box = data.get("box")
        if isinstance(box, list) and len(box) == 4:
            media["region"] = {"type": "box", "coordinates": [float(v) for v in box], "units": "normalized"}
        ev["media"] = media
        yield start, ev


EXIF_TIME = re.compile(r"^(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def read_megadetector(s: Settings, cursor):
    """Folder of MegaDetector batch output files (also written by AddaxAI, EcoAssist and other camera trap tools)
    that include a species classifier's results. One event per image and species, best score kept. MegaDetector
    alone only says 'animal', so files without classifications send nothing. Cursor maps each file to its mtime."""
    dep = deployment(s, None, None, "camera-trap", s.sensor_model or "MegaDetector")
    if dep is None:
        sys.exit("camera traps need latitude/longitude in the config")
    species_map = load_species_map(s)
    seen = dict(cursor or {})
    sent = 0
    for f in sorted(Path(s.path).glob("**/*.json")):
        mtime = f.stat().st_mtime
        if seen.get(str(f)) == mtime:
            continue
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("images"), list):
            continue
        det_cats = doc.get("detection_categories") or {"1": "animal"}
        cls_cats = doc.get("classification_categories") or {}
        descs = doc.get("classification_category_descriptions") or {}
        info = doc.get("info") or {}
        clf_name = s.classifier or info.get("classifier") or "species classifier"
        clf_version = s.classifier_version if s.classifier_version != "unknown" else str(info.get("classifier_version") or info.get("detector") or "unknown")
        file_events = []
        for img in doc["images"]:
            best = {}
            for d in img.get("detections") or []:
                if det_cats.get(str(d.get("category")), "") != "animal" or float(d.get("conf") or 0) < s.min_detection:
                    continue
                for cid, score in (d.get("classifications") or [])[:1]:
                    label = descs.get(str(cid)) or cls_cats.get(str(cid), "")
                    taxon = taxon_from_label(label, species_map) or taxon_from_label(cls_cats.get(str(cid), ""), species_map)
                    if not taxon or float(score) < s.min_confidence:
                        continue
                    if taxon[0] not in best or score > best[taxon[0]][1]:
                        best[taxon[0]] = (taxon[1], float(score), d.get("bbox"))
            if not best:
                continue
            name = str(img.get("file", ""))
            m = EXIF_TIME.match(str(img.get("datetime") or ""))
            if m:
                when = datetime(*(int(g) for g in m.groups())).astimezone()
            else:
                img_path = name if os.path.isabs(name) else str(f.parent / name)
                try:
                    when = recording_start(s, name, img_path)
                except OSError:
                    when = datetime.fromtimestamp(mtime).astimezone()
            for sci, (common, score, bbox) in best.items():
                rid = hashlib.sha256(f"{name}|{sci}".encode()).hexdigest()[:16]
                ev = base_event(s, f"{s.system}:{s.station_id}:{rid}", when.isoformat(timespec="seconds"), dep, rid)
                ev["detection"] = detection(sci, common, score, clf_name, clf_version)
                media = {"mediaType": "image", "fileName": os.path.basename(name) or "unknown.jpg"}
                if isinstance(bbox, list) and len(bbox) == 4:
                    media["region"] = {"type": "box", "coordinates": [float(v) for v in bbox], "units": "normalized"}
                ev["media"] = media
                file_events.append(ev)
        seen[str(f)] = mtime
        if not file_events:
            yield dict(seen), None  # nothing to send, but remember the file
            continue
        for ev in file_events:
            sent += 1
            yield dict(seen), ev
        if sent >= BATCH:
            break


SOURCES = {"birdnet-pi": read_birdnet_pi, "birdnet-go": read_birdnet_go, "speciesnet": read_speciesnet,
           "batdetect2": read_batdetect2, "csv": read_csv, "ndjson": read_ndjson,
           "frigate": read_frigate, "megadetector": read_megadetector}


def post(s: Settings, events: list) -> dict:
    body = "\n".join(json.dumps(e, separators=(",", ":")) for e in events).encode()
    req = urllib.request.Request(s.endpoint, data=body, method="POST", headers={
        "authorization": f"Bearer {s.api_key}", "content-type": "application/x-ndjson", "user-agent": f"wdx-agent/{VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def state_key(s: Settings) -> str:
    return f"{s.source}:{s.path}"


def load_state(s: Settings) -> dict:
    try:
        return json.loads(Path(s.state_path).read_text())
    except (OSError, ValueError):
        return {}


def save_cursor(s: Settings, cursor) -> None:
    """Advance the source cursor. Also used by the Wildlife Network Base after a phone has carried a batch out."""
    state = load_state(s)
    state[state_key(s)] = cursor
    tmp = s.state_path + ".tmp"
    Path(tmp).write_text(json.dumps(state))
    os.replace(tmp, s.state_path)


def pending_batch(s: Settings) -> list:
    """Up to BATCH (cursor, event) pairs not yet sent; event is None for rows that are skipped but still move the cursor."""
    return list(SOURCES[s.source](s, load_state(s).get(state_key(s))))


def run_once(s: Settings, dry: bool) -> int:
    """Sends one batch. Returns the rows read (BATCH or more: keep draining); 0 when nothing was sent."""
    batch = pending_batch(s)
    if not batch:
        return 0
    # None marks a row that was skipped but still moves the cursor; so do rows at or before start_after.
    events = [e for _, e in batch if e is not None and after_start(s, e)]
    if dry:
        for e in events[:5]:
            print(json.dumps(e, indent=2))
        log(f"dry run: {len(events)} events ready (showing up to 5)")
        return len(events)
    res = post(s, events) if events else {"received": 0, "inserted": 0, "updated": 0, "rejected": 0}
    PROGRESS.events(res)
    log(f"sent {res.get('received')} (new {res.get('inserted')}, updated {res.get('updated')}, rejected {res.get('rejected')})")
    if res.get("rejected"):
        log(f"rejected sample: {json.dumps(res.get('rejectedDetails', [])[:2])}")
    if events and not res.get("received") and res.get("rejected"):
        log("server rejected the whole batch; not advancing (check api_key and source in the config)")
        return 0
    # The cursor only advances after the server accepted the batch, so an offline Pi just catches up later.
    save_cursor(s, batch[-1][0])
    # Rows read, not events sent: a full batch means more rows may be waiting, even when most were skipped (below the
    # threshold, unlikely, or before start_after), so the main loop keeps reading instead of waiting a whole interval.
    return len(batch)


SUMMARY_EVERY = 3600     # seconds between "sent so far" lines in the log
NETWORK_RETRY = 60       # after a network error, try again at least this often (status and events)


class Progress:
    """Counts what was sent, for an info line once an hour and right after the first success after a network
    failure, so the log shows that uploads work, not only when they fail."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()
        self.failures = 0
        self.last_summary = clock()

    def reset(self):
        self.sent = self.inserted = self.rejected = self.status = 0

    def events(self, res: dict) -> None:
        self.sent += int(res.get("received") or 0)
        self.inserted += int(res.get("inserted") or 0)
        self.rejected += int(res.get("rejected") or 0)

    def status_sent(self) -> None:
        self.status += 1

    def failed(self) -> None:
        self.failures += 1

    def ok(self) -> str | None:
        """Call after a pass without errors. Returns the summary line when one is due (and logs it)."""
        recovered = self.failures > 0
        if not recovered and self.clock() - self.last_summary < SUMMARY_EVERY:
            return None
        mins = max(1, round((self.clock() - self.last_summary) / 60))
        line = (f"summary: {self.sent} events sent ({self.inserted} new, {self.rejected} rejected) and "
                f"{self.status} status reports in the last {mins} min")
        if recovered:
            line = f"connection back after {self.failures} failed attempt(s); " + line
        log(line)
        self.reset()
        self.failures = 0
        self.last_summary = self.clock()
        return line


def retry_wait(err: BaseException, backoff: int, interval: int) -> int:
    """Seconds to wait after an error. A server answer (HTTP error) backs off up to an hour; a network error
    (no route, DNS, timeout) retries within a minute, so the agent catches up soon after the network returns."""
    if isinstance(err, urllib.error.HTTPError):
        return min(backoff * 2, 3600)
    return min(max(interval, 1), NETWORK_RETRY)


PROGRESS = Progress()

LATEST: dict = {"version": None, "checked": 0.0}


def newer(a: str, b: str) -> bool:
    """True when version a is newer than b ("0.4.10" > "0.4.9")."""
    def parts(v: str) -> tuple:
        return tuple(int(x) if x.isdigit() else 0 for x in v.strip().split("."))
    return parts(a) > parts(b)


def check_latest(s: Settings) -> None:
    """Once a day: ask the server which agent version is current and say so in the log when this one is older."""
    if time.time() - LATEST["checked"] < 86_400:
        return
    LATEST["checked"] = time.time()
    url = s.endpoint.split("/api/", 1)[0] + "/agent/VERSION"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": f"wdx-agent/{VERSION}"}), timeout=15) as r:
            latest = r.read(32).decode().strip()
    except (urllib.error.URLError, TimeoutError, ValueError):
        return
    if latest and all(p.isdigit() for p in latest.split(".")):
        LATEST["version"] = latest
        if newer(latest, VERSION):
            log(f"wdx-agent {latest} is available (this is {VERSION}). Run the install command again to update; your settings are kept.")


def collect_status(s: Settings) -> dict:
    """WDX device-status record: what can be read on any Linux device, plus the battery if a command is configured."""
    st = {"wdx": "0.2", "kind": "device-status", "deviceId": s.device_id or s.station_id,
          "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
          "software": {"wdx-agent": VERSION, **({"latest": LATEST["version"]} if LATEST["version"] else {})}}
    try:
        du = shutil.disk_usage(os.path.dirname(s.state_path) or "/")
        st["storage"] = {"freeMb": du.free // 1_000_000, "totalMb": du.total // 1_000_000}
    except OSError:
        pass
    try:
        st["temperatureC"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        st["uptimeSeconds"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    if s.battery_command:
        try:
            out = subprocess.run(s.battery_command, shell=True, capture_output=True, text=True, timeout=10).stdout
            b = json.loads(out)
            st["battery"] = {k: b[k] for k in ("percent", "volts", "charging") if k in b}
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    try:
        # Rows waiting to be sent: a cheap upper bound from one batch read (BATCH means "at least this many").
        st["queue"] = {"pending": sum(1 for _, e in pending_batch(s) if e is not None)}
    except (OSError, sqlite3.Error, KeyError, SystemExit):
        pass  # no source yet, or a source that needs coordinates before it can be read: report the rest
    return st


def post_status(s: Settings) -> None:
    body = json.dumps(collect_status(s), separators=(",", ":")).encode()
    req = urllib.request.Request(s.status_endpoint, data=body, method="POST", headers={
        "authorization": f"Bearer {s.api_key}", "content-type": "application/json", "user-agent": f"wdx-agent/{VERSION}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main() -> None:
    ap = argparse.ArgumentParser(description="Push wildlife detections to a WDX endpoint")
    ap.add_argument("--config", default="/etc/wdx-agent.ini")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = Settings(args.config)
    if s.source not in SOURCES:
        sys.exit(f"unknown source {s.source}; choose one of {', '.join(SOURCES)}")
    if not s.api_key and not args.dry_run:
        sys.exit("api_key is empty: register at the endpoint or run install.sh")
    log(f"wdx-agent {VERSION} source={s.source} path={s.path} station={s.station_id} -> {s.endpoint}")
    backoff = s.interval
    last_status = 0.0
    while True:
        pass_failed = False
        try:
            if s.device_id and not args.dry_run and time.time() - last_status >= s.status_interval:
                last_status = time.time()
                try:
                    post_status(s)
                    PROGRESS.status_sent()
                except (urllib.error.URLError, TimeoutError) as e:
                    log(f"status not sent: {e}; retrying with the next attempt")
                    last_status = 0.0   # not the next 15-minute window: as soon as the network is back
                    PROGRESS.failed()
                    pass_failed = True
            if not args.dry_run:
                check_latest(s)
            n = run_once(s, args.dry_run)
            backoff = s.interval
            if not args.dry_run and not pass_failed:
                PROGRESS.ok()
            if args.once or args.dry_run:
                return
            if n >= BATCH:
                continue  # more waiting, keep draining
        except (urllib.error.URLError, TimeoutError, sqlite3.OperationalError) as e:
            detail = e.read().decode()[:200] if isinstance(e, urllib.error.HTTPError) else str(e)
            backoff = retry_wait(e, backoff, s.interval)
            log(f"error: {detail}; retrying in {backoff}s")
            if not isinstance(e, sqlite3.OperationalError):
                PROGRESS.failed()
            if args.once:
                sys.exit(1)
        time.sleep(backoff)


if __name__ == "__main__":
    main()

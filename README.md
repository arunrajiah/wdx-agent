# wdx-agent

Send the wildlife detections your device already makes to an open, global map.

`wdx-agent` is a small, free program for a Raspberry Pi (or any Linux or macOS machine). It reads detections from BirdNET-Pi, BirdNET-Go, a camera trap classifier, or a bat detector, converts them to the open [WDX](https://github.com/arunrajiah/wildlife-detection-exchange) format, and pushes them to [WildNetwork](https://wildnetwork.arunrajiah.com) or any other WDX endpoint.

- One Python file. Standard library only. No pip, no Docker.
- Works offline: it catches up by itself when the network returns.
- Private by default: coordinates are rounded to about 1 km before they leave your device.
- Apache 2.0 licensed.

## Contents

- [Quick start](#quick-start)
- [Capture setups](#capture-setups)
  - [Acoustic station with BirdNET-Pi](#acoustic-station-with-birdnet-pi)
  - [Acoustic station with BirdNET-Go](#acoustic-station-with-birdnet-go)
  - [Camera trap with SpeciesNet](#camera-trap-with-speciesnet)
  - [Bat detector with BatDetect2](#bat-detector-with-batdetect2)
  - [Any detections table (CSV)](#any-detections-table-csv)
  - [Anything else: write WDX to a file](#anything-else-write-wdx-to-a-file)
- [Configuration reference](#configuration-reference)
- [Privacy](#privacy)
- [How it behaves](#how-it-behaves)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Contributing and governance](#contributing-and-governance)

## Quick start

On a Raspberry Pi that already runs BirdNET-Pi or BirdNET-Go:

```bash
curl -fsSL https://wildnetwork.arunrajiah.com/agent/install.sh | bash
```

The installer:

1. finds your BirdNET database,
2. registers a device key for your station (no account needed),
3. writes `/etc/wdx-agent.ini`,
4. installs and starts a `wdx-agent` systemd service.

Your detections appear on the map within a minute or two. Watch the agent work:

```bash
journalctl -u wdx-agent -f
```

Prefer to read before you run? The installer is [install.sh](install.sh), about 80 lines. You can also do every step by hand, see [Manual install](#manual-install).

## Capture setups

You need two things: something that detects animals, and this agent to share what it finds.

### Acoustic station with BirdNET-Pi

**Hardware:** Raspberry Pi 4B, 5, 3B+ or Zero 2 W, a 32 GB or larger microSD card, a USB microphone or a USB sound card with a lavalier microphone, and a weatherproof box if it lives outside. Power over USB or PoE.

**Software:** install [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) following its guide, and set your latitude and longitude in its settings. BirdNET-Pi stores every detection in `~/BirdNET-Pi/scripts/birds.db`.

**Share:** run the quick start command. The agent reads the `detections` table and uses the coordinates stored with each detection.

### Acoustic station with BirdNET-Go

**Hardware:** same as above. BirdNET-Go also runs well on a mini PC or NAS, and can listen to RTSP streams from IP cameras.

**Software:** install [BirdNET-Go](https://github.com/tphakala/birdnet-go). It stores detections in a SQLite file named `birdnet.db` (location depends on how you installed it).

**Share:** run the quick start command. If the installer cannot find the database, tell it where it is:

```bash
curl -fsSL https://wildnetwork.arunrajiah.com/agent/install.sh | WDX_SOURCE=birdnet-go WDX_PATH=/path/to/birdnet.db bash
```

The agent reads the `notes` table. BirdNET-Go's newer v2 datastore is not supported yet, see the open issues.

### Camera trap with SpeciesNet

**Hardware:** any trail camera. Copy images from the SD card to a computer or Pi, or use a cellular camera that uploads to a folder.

**Software:** classify the images with [SpeciesNet](https://github.com/google/cameratrapai):

```bash
pip install speciesnet
python -m speciesnet.scripts.run_model --folders /data/camtrap/2026-10 --predictions_json /data/camtrap/2026-10/predictions.json
```

**Share:** point the agent at the folder that holds the `predictions.json` files. Camera traps have no GPS in the file the agent reads, so give the location:

```bash
curl -fsSL https://wildnetwork.arunrajiah.com/agent/install.sh | WDX_SOURCE=speciesnet WDX_PATH=/data/camtrap WDX_LAT=11.41 WDX_LON=76.69 bash
```

The agent skips blanks, humans and vehicles, and anything below `min_confidence`. The event time is the image file's modification time, so keep timestamps when copying (`cp -p`, `rsync -t`). One agent serves one location: for several cameras, run one config per camera with its own coordinates.

For sensitive species, raise the rounding (`round_coords = 1` is about 11 km) or do not share that camera.

### Bat detector with BatDetect2

**Hardware:** an ultrasonic recorder. An [AudioMoth](https://www.openacousticdevices.info/audiomoth) at a 250 kHz or higher sample rate is the common low cost choice; Song Meter Mini Bat, Pettersson and Batlogger units work the same way. Schedule it from dusk to dawn.

**Software:** [BatDetect2](https://github.com/macaodha/batdetect2) finds and classifies echolocation calls. It writes one result file per recording:

```bash
pip install batdetect2
batdetect2 detect /data/bats/2026-09-30 /data/bats/results 0.3
```

**Share:** point the agent at the results folder and give the detector's location.

```bash
curl -fsSL https://wildnetwork.arunrajiah.com/agent/install.sh | WDX_SOURCE=batdetect2 WDX_PATH=/data/bats/results WDX_LAT=51.51 WDX_LON=-0.13 bash
```

How the agent treats bat data:

- BatDetect2 reports every single call. A bat flying past produces many, so the agent sends **one event per species per recording**, with the highest class probability, and only if at least `min_calls` calls (default 2) pass `min_confidence`.
- The recording's start time is read from its file name (`20260930_213000.WAV`). AudioMoth names files in UTC; if your recorder uses local time, set `filename_timezone = local`. Without a time in the name, the file's modification time is used.
- BatDetect2's default model is trained on UK bat species. Elsewhere, use a model trained for your species, or expect wrong names.
- Species level identification of bats from calls is uncertain, particularly within *Myotis*. Consider a higher `min_confidence` (0.8) for bats.

### Any detections table (CSV)

Kaleidoscope, SonoBat, the BTO Acoustic Pipeline and most other tools can export a table of detections. The `csv` source reads any of them: you tell it which columns hold what. Check the names against your own file's header row.

```ini
[agent]
source = csv
path = /data/bats/id.csv
latitude = 40.02
longitude = -75.31
sensor_model = Song Meter Mini Bat
classifier = Kaleidoscope Pro
classifier_version = 5.6

csv_species = AUTO ID
csv_confidence = MATCH RATIO
csv_date = DATE
csv_time = TIME
csv_datetime_format = %Y-%m-%d %H:%M:%S
csv_file = IN FILE
; optional: a two column file (code, scientific name) when the table uses codes such as EPTFUS
csv_species_map = /data/bats/codes.csv
```

| Key | Meaning |
|---|---|
| `csv_species` | Column with the species. Scientific names are best. Required. |
| `csv_datetime`, or `csv_date` + `csv_time` | When. ISO 8601 is read directly; otherwise give `csv_datetime_format`. Required. |
| `csv_datetime_format` | A Python `strptime` format, for example `%d/%m/%Y %H:%M:%S`. |
| `csv_timezone` | `local` (default) or `utc`, for times without an offset. |
| `csv_confidence`, `csv_confidence_scale` | Score column, and `100` if it is a percentage. Without a column, every row counts as 1.0. |
| `csv_common`, `csv_file`, `csv_latitude`, `csv_longitude` | Optional columns. |
| `csv_species_map` | Two column CSV translating codes to scientific names. |
| `csv_delimiter` | `,` by default; `\t` for tab separated files. |
| `sensor_type` | `acoustic-recorder` (default) or `camera-trap`. |
| `system` | What the server records as the source. `other` by default; register your key with the same value. |

Rows marked `NoID`, `Noise` or `Unknown`, rows below `min_confidence`, and rows that cannot be read are skipped. The agent remembers how many rows it has sent, so **only append** to the file: do not re-sort or rewrite it.

### Anything else: write WDX to a file

Frigate, MegaDetector, Animl, a custom model, a notebook: if your tool can append one JSON object per line to a file, the agent can ship it.

```ini
source = ndjson
path = /var/lib/mytool/events.wdx.ndjson
```

Each line must be a valid [WDX 0.1 event](https://github.com/arunrajiah/wildlife-detection-exchange/blob/main/SPEC.md). Minimal example:

```json
{"wdx":"0.1","eventId":"mytool:cam7:000123","eventStart":"2026-10-01T06:12:03+05:30","deployment":{"deploymentId":"cam7","latitude":13.08,"longitude":80.27,"sensorType":"camera-trap"},"detection":{"scientificName":"Panthera pardus","confidence":0.93,"classifier":{"name":"MyModel","version":"1.2"}},"source":{"system":"other"}}
```

Register the key with `"source": "other"` and set `source.system` to `other` in your events.

## Manual install

```bash
sudo mkdir -p /opt/wdx-agent
sudo curl -fsSL https://raw.githubusercontent.com/arunrajiah/wdx-agent/main/wdx_agent.py -o /opt/wdx-agent/wdx_agent.py

# get a device key (shown once)
curl -X POST https://wildnetwork.arunrajiah.com/api/v1/register \
  -H 'content-type: application/json' \
  -d '{"name":"my-garden","source":"birdnet-pi"}'

sudo nano /etc/wdx-agent.ini     # see the reference below
python3 /opt/wdx-agent/wdx_agent.py --config /etc/wdx-agent.ini --dry-run   # prints events, sends nothing
python3 /opt/wdx-agent/wdx_agent.py --config /etc/wdx-agent.ini --once      # sends one batch
```

Then install [wdx-agent.service](wdx-agent.service) into `/etc/systemd/system/`, replace `__USER__` with your user name, and run `sudo systemctl enable --now wdx-agent`.

## Configuration reference

`/etc/wdx-agent.ini`

| Key | Default | Meaning |
|---|---|---|
| `endpoint` | `https://wildnetwork.arunrajiah.com/api/v1/events` | Where events are sent. Any WDX endpoint works. |
| `api_key` | none | Device key from registration. Tied to one `source`. |
| `source` | `birdnet-pi` | `birdnet-pi`, `birdnet-go`, `speciesnet`, `batdetect2`, `csv` or `ndjson`. |
| `path` | none | Database file, predictions folder, or NDJSON file. |
| `station_id` | derived from hostname and machine id | Stable id for this device. |
| `station_name` | empty | Optional public label. Leave empty to stay anonymous. |
| `latitude`, `longitude` | empty | Used when the source has no coordinates (always for camera traps). |
| `round_coords` | `2` | Decimals kept. 2 is about 1 km, 1 is about 11 km, 3 is about 110 m. |
| `min_confidence` | `0.7` | Detections below this are never sent. |
| `min_calls` | `2` | `batdetect2` only: calls needed in a recording before a species is reported. |
| `filename_timezone` | `utc` | `batdetect2` only: `utc` or `local`, for the time in recording file names. |
| `interval_seconds` | `60` | How often to look for new detections. |
| `media_base_url` | empty | If your clips are publicly reachable, the URL prefix to link them. |
| `license` | CC BY 4.0 | License you grant on the detections you share. |
| `state_file` | `~/.wdx-agent-state.json` | Where the agent remembers what it already sent. |

## Privacy

- **Location:** coordinates are rounded on your device, before sending. The server never sees the precise position. Each event also carries the resulting uncertainty in metres.
- **What is sent:** species name, confidence, time, rounded location, classifier name, a file name for the clip or image, and your station id and optional name. No audio, no images, no IP-derived data in the event itself.
- **What is not sent:** recordings and photos stay on your device unless you set `media_base_url` yourself.
- **Your data:** you choose the license (`license` key). The default, CC BY 4.0, lets others use the detections with credit.
- **Sensitive species:** if you record threatened species at a known site, use `round_coords = 1` or do not share.

## How it behaves

- Sends new detections in batches of up to 500.
- The cursor moves forward only after the server accepts a batch. A Pi that was offline for a week sends that week when it reconnects.
- Event ids are stable (`source:station:row`), so sending twice never creates duplicates.
- On errors it retries with a growing delay, up to one hour.
- The database is opened read-only. The agent never writes to BirdNET's files.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `config not found` | Pass `--config /etc/wdx-agent.ini`, or create the file. |
| `no coordinates` | The source rows have no location. Set `latitude` and `longitude`. |
| `server rejected the whole batch` | The key belongs to a different `source`. Register a key for the right one. |
| `401` in the log | `api_key` is wrong or was revoked. |
| `unable to open database file` | Wrong `path`, or the service user cannot read it. Check `User=` in the service file. |
| Nothing is sent | Run with `--dry-run`. If it prints 0 events, everything is below `min_confidence` or already sent. |
| Start over | Stop the service, delete the state file, start again. Duplicates are ignored by the server. |

## Uninstall

```bash
sudo systemctl disable --now wdx-agent
sudo rm -rf /opt/wdx-agent /etc/wdx-agent.ini /etc/systemd/system/wdx-agent.service /var/lib/wdx-agent
sudo systemctl daemon-reload
```

## Contributing and governance

New sources are the most useful contribution: Frigate, MegaDetector, Animl, BirdNET-Go v2, and presets for bat software exports. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add one, [GOVERNANCE.md](GOVERNANCE.md) for how decisions are made, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## License and credits

Apache License 2.0, see [LICENSE](LICENSE). Made by [Arun Rajiah](https://www.arunrajiah.com).

Built for the work of others: [BirdNET](https://birdnet.cornell.edu) (Cornell Lab of Ornithology and Chemnitz University of Technology), [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi), [BirdNET-Go](https://github.com/tphakala/birdnet-go), [SpeciesNet](https://github.com/google/cameratrapai) (Google), and [BatDetect2](https://github.com/macaodha/batdetect2) (Mac Aodha et al.).

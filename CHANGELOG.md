# Changelog

## 0.4.8 (2026-11-22)

- Catches up much faster when many rows are skipped. The agent only went straight on to the next batch when the last one sent 500 detections; a batch of mostly skipped rows (below `min_confidence`, unlikely for the location, or before `start_after`) made it wait a whole interval. A station that installs the agent with `start_after` and a large history now reaches new detections in seconds or minutes instead of hours.

## 0.4.7 (2026-11-22)

- New `start_after` setting (ISO time): detections at or before it are skipped, and the cursor still moves past them. The installer sets it from `WDX_START_AFTER`, and `station_id` from `WDX_STATION_ID`. BirdEcho passes both when its phone already shared the station's detections, so installing the agent afterwards does not send them a second time.

## Installer (2026-11-22)

- Finds the BirdNET database in more places: besides the usual install folders it now searches /home, /opt, /srv, /data, /mnt, /media and Docker volumes, so BirdNET-Go set up in Docker or on a NAS is found without `WDX_PATH`.
- `WDX_SOURCE=birdnet-go` (or `birdnet-pi`) without `WDX_PATH` now searches for that database instead of stopping.
- When no database is found, the message says to run the installer on the computer that runs BirdNET and shows how to find the file.

## 0.4.6 (2026-11-20)

- BirdNET-Go, older `notes` datastore: new key `notes_zone_before` (`<id> <zone>` pairs) for rows written before a change of time zone, so a detection still waiting to be sent when the zone changes keeps its true time. The Wildlife Network Base sets it when it switches from UTC to the station's own zone. The newer `detections` datastore needs nothing: its times are Unix timestamps and stay correct in any zone (now covered by tests in UTC, India and New York time).

## Installer (2026-11-20)

- Re-running `install.sh` to update now restarts the agent when it is already running, so the new version takes over (before, the old version kept running until a reboot). It ends by printing the version that is running.
- The installer no longer replaces a `wdx-agent.service` set up by something else, such as a Wildlife Network Base; it only writes the file when it is missing or is the installer's own.
- A fresh install without `WDX_LAT`/`WDX_LON` no longer fails its own check: the empty `latitude =` and `longitude =` lines (which cannot be read as numbers) are now written commented out.
- A service stuck in a failed state, or at systemd's start limit after several quick restarts, is reset before the installer starts it.

## 0.4.5 (2026-11-19)

- The log now shows that uploads work: once an hour, and right after the first success following a network failure, an info line sums up events sent (new and rejected) and status reports sent since the last one.
- After a network error the agent tries again within a minute (or `interval_seconds` if shorter), for events and for the status report, instead of backing off up to an hour or waiting for the next 15-minute status window. Errors from the server itself still back off up to an hour.

## 0.4.4 (2026-11-19)

- BirdNET-Go: the database layout is detected explicitly (current `detections` + `labels`, or the older `notes`). An unknown layout is logged once as a warning and nothing is sent, instead of failing on every pass.
- New key `test` (default `no`): when `yes`, every event carries `"test": true`, and Wildlife Network keeps those events off the public map. The Wildlife Network Base sets it from its "Test station" option.

## 0.4.3 (2026-11-16)

- Once a day the agent asks the server for the current version (`/agent/VERSION`). When it is older, the log says so and how to update (run the install command again; settings are kept), and health reports include `software.latest`.

## 0.4.2 (2026-11-16)

- Installer: `WDX_DEVICE_ID` (BirdEcho passes it) sets `device_id`, which turns on health reports, so the app and wlnw.org/stations can show when the station last reported.

## 0.4.1 (2026-11-15)

- Installer: running it again keeps the existing key instead of registering a new one, takes a new key when `WDX_KEY` is given, and moves agents still pointing at wildnetwork.arunrajiah.com to the current server.
- Installer: checks that the detections can be read and that the server accepts the key before starting the service, and says plainly what to fix when not.

## 0.4.0 (2026-11-15)

- New source `megadetector`: camera trap results in the MegaDetector batch output format with a species classifier (AddaxAI, EcoAssist and similar). One event per image and species, with the animal's box.
- New source `frigate`: Frigate NVR events over its HTTP API, using the bird classification sub label (common names turned into scientific names with Frigate's own label list) or a species map for labels such as `deer`. Only finished events are sent.
- New keys: `species_map`, `min_detection`, `api_token`, `frigate_labels_url`.

## 0.3.2 (2026-10-23)

- WildNetwork moved to wlnw.org: the default endpoint and installer now use https://wlnw.org. Agents already installed keep working, because wildnetwork.arunrajiah.com still accepts uploads; to switch, set `endpoint = https://wlnw.org/api/v1/events` in /etc/wdx-agent.ini or reinstall.

## 0.3.1 (2026-10-08)

- Health reports are sent even when the source cannot be read yet (for example no coordinates set); the queue count is then left out.

## 0.3.0 (2026-10-08)

- Health reports: with `device_id` set, the agent sends a WDX device-status record (storage, temperature, uptime, queue, battery via `battery_command`) every `status_interval_seconds`.
- Helpers `pending_batch`, `save_cursor` and `collect_status` for the WildNetwork Base, which lets a phone carry detections out when there is no internet.

## 0.2.1 (2026-10-07)

- BirdNET-Go: reads the current datastore (`detections`, `labels`, `ai_models`); the old `notes` table still works. Migrated detections keep their old event ids. Detections flagged unlikely and non-species labels are skipped.

## 0.2.0 (2026-10-02)

- New source `batdetect2`: bat detectors. Reads BatDetect2 result files and groups calls into one event per species per recording.
- New source `csv`: any detections table, with column names set in the config, optional code to name mapping.
- Config values are read without interpolation, so date formats can be written as `%Y-%m-%d`.
- New keys: `system`, `sensor_type`, `sensor_model`, `classifier`, `classifier_version`, `min_calls`, `filename_timezone`.

## 0.1.0 (2026-10-01)

- First release.
- Sources: BirdNET-Pi, BirdNET-Go (v1 datastore), SpeciesNet predictions folder, WDX NDJSON file.
- Installer with automatic device registration and systemd service.
- Coordinate rounding on the device, offline catch-up, stable event ids.

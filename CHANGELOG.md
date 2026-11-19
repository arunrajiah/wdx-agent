# Changelog

## Installer (2026-11-20)

- Re-running `install.sh` to update now restarts the agent when it is already running, so the new version takes over (before, the old version kept running until a reboot). It ends by printing the version that is running.
- The installer no longer replaces a `wdx-agent.service` set up by something else, such as a Wildlife Network Base; it only writes the file when it is missing or is the installer's own.

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

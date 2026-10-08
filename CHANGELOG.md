# Changelog

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

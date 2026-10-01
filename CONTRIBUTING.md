# Contributing to wdx-agent

Thanks for helping more devices share what they hear and see.

## Ground rules

- **Standard library only.** The agent must run on a stock Raspberry Pi OS with no `pip install`. Pull requests that add a dependency will be declined.
- **One file.** `wdx_agent.py` stays a single file so people can read it before running it.
- **Python 3.9+.** BirdNET-Pi still runs on older Pi OS releases.
- **Never write to the source.** Databases are opened read-only.
- **Privacy first.** Anything that changes what leaves the device needs a clear note in the pull request and the README.

## Development

```bash
git clone https://github.com/arunrajiah/wdx-agent && cd wdx-agent
python3 -m unittest discover -s tests          # run the tests
python3 wdx_agent.py --config my.ini --dry-run  # print events without sending
```

## Adding a source

A source is one generator function plus one line in `SOURCES`:

```python
def read_mytool(s: Settings, cursor):
    """Yield (cursor, event) pairs. The cursor must increase and be JSON serialisable."""
    for row in new_rows_after(cursor):
        dep = deployment(s, row.lat, row.lon, "camera-trap", "MyTool")
        ev = base_event(s, f"{s.source}:{s.station_id}:{row.id}", iso_time_with_offset, dep, str(row.id))
        ev["detection"] = {"scientificName": ..., "confidence": ..., "classifier": {"name": ..., "version": ...}}
        yield row.id, ev
```

Checklist for a new source:

1. Event ids are stable across restarts, so re-sending never duplicates.
2. `eventStart` carries a UTC offset.
3. Confidence is passed through as the classifier reports it (0 to 1), never rescaled.
4. At most `BATCH` events per call.
5. A test in `tests/test_agent.py` that builds a tiny fixture and checks the mapping.
6. A section in the README under "Capture setups".

The server only issues keys for known source names. Open an issue first so the name can be added on the WildNetwork side.

## Pull requests

- Keep them small and focused. One source or one fix per pull request.
- Tests must pass (`python3 -m unittest discover -s tests`). CI runs them on Python 3.9 and 3.12.
- Sign off your commits (`git commit -s`). This is the [Developer Certificate of Origin](https://developercertificate.org): you confirm you have the right to contribute the code under the Apache 2.0 license.

## Reporting bugs

Use the bug report template. Include the agent version (first log line), your source, and the output of a `--dry-run`, with your API key removed.

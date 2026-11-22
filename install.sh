#!/usr/bin/env bash
# wdx-agent installer for Raspberry Pi / Linux.
#   curl -fsSL https://wlnw.org/agent/install.sh | bash
# Detects BirdNET-Pi or BirdNET-Go, registers a device key, and installs a systemd service.
# Override anything with env vars: WDX_SOURCE, WDX_PATH, WDX_LAT, WDX_LON, WDX_NAME, WDX_KEY, WDX_BASE,
# WDX_DEVICE_ID (from BirdEcho or the device registry: turns on health reports, so the app can show it is sending),
# WDX_STATION_ID and WDX_START_AFTER (from BirdEcho when its phone already shared this station: same station, and
# detections up to that time are not sent again).
set -euo pipefail

BASE="${WDX_BASE:-https://wlnw.org}"
CONF=/etc/wdx-agent.ini
RUN_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo "~$RUN_USER")"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

SOURCE="${WDX_SOURCE:-}"; DBPATH="${WDX_PATH:-}"
# First match of a database file: the usual install folders, then a wider search (Docker volumes, NAS shares, /opt).
find_db() {
  local name="$1"; shift
  for p in "$@"; do [ -f "$p" ] && { echo "$p"; return; }; done
  local roots=""; for r in /home /root /opt /srv /data /mnt /media /var/lib/docker/volumes; do [ -d "$r" ] && roots="$roots $r"; done
  [ -n "$roots" ] || return 0
  { find $roots -maxdepth 6 -type f -name "$name" 2>/dev/null || sudo -n find $roots -maxdepth 6 -type f -name "$name" 2>/dev/null; } | grep -v '/proc/' | head -1 || true
}
if [ -z "$DBPATH" ] && { [ -z "$SOURCE" ] || [ "$SOURCE" = birdnet-pi ]; }; then
  p="$(find_db birds.db "$HOME_DIR/BirdNET-Pi/scripts/birds.db" /home/*/BirdNET-Pi/scripts/birds.db)"
  [ -n "$p" ] && { SOURCE=birdnet-pi; DBPATH="$p"; }
fi
if [ -z "$DBPATH" ] && { [ -z "$SOURCE" ] || [ "$SOURCE" = birdnet-go ]; }; then
  p="$(find_db birdnet.db "$HOME_DIR/birdnet-go-app/data/birdnet.db" "$HOME_DIR/birdnet-go/birdnet.db" /data/birdnet.db /home/*/birdnet-go-app/data/birdnet.db)"
  [ -n "$p" ] && { SOURCE=birdnet-go; DBPATH="$p"; }
fi
if [ -z "$SOURCE" ] || { [ -z "$DBPATH" ] && { [ "$SOURCE" = birdnet-pi ] || [ "$SOURCE" = birdnet-go ]; }; }; then
  echo "No ${SOURCE:-BirdNET-Pi or BirdNET-Go} database found on this computer."
  echo "Run this installer on the computer that runs BirdNET (not your phone or laptop)."
  echo "If BirdNET-Go keeps its data somewhere else (for example a Docker volume or a NAS share), find the file with:"
  echo "  sudo find / -name birdnet.db 2>/dev/null"
  echo "and run the installer again with WDX_PATH=<that path> in front of bash. More help: $BASE/contribute"
  exit 1
fi
say "Found $SOURCE at $DBPATH"

NAME="${WDX_NAME:-$(hostname)}"
KEY="${WDX_KEY:-}"
# Re-running the installer keeps the existing key unless a new one is given (no second registration).
if [ -z "$KEY" ] && [ -f "$CONF" ]; then
  KEY="$(sudo sed -n 's/^api_key *= *//p' "$CONF" | head -1)"
fi
if [ -z "$KEY" ]; then
  REG_SOURCE="$SOURCE"; { [ "$SOURCE" = ndjson ] || [ "$SOURCE" = csv ]; } && REG_SOURCE=other
  say "Registering this device with $BASE"
  KEY="$(python3 - "$BASE" "$NAME" "$REG_SOURCE" <<'PY'
import json, sys, urllib.request
base, name, source = sys.argv[1:4]
req = urllib.request.Request(base + "/api/v1/register", data=json.dumps({"name": name, "source": source}).encode(),
                             headers={"content-type": "application/json"}, method="POST")
try:
    print(json.loads(urllib.request.urlopen(req, timeout=30).read())["apiKey"])
except Exception as e:
    sys.stderr.write(f"registration failed: {e}\n"); sys.exit(1)
PY
)"
fi

say "Installing to /opt/wdx-agent"
sudo mkdir -p /opt/wdx-agent
sudo curl -fsSL "$BASE/agent/wdx_agent.py" -o /opt/wdx-agent/wdx_agent.py
sudo chmod 755 /opt/wdx-agent/wdx_agent.py

if [ ! -f "$CONF" ]; then
  # An empty "latitude =" cannot be read as a number, so leave the lines commented out when none are given.
  LAT_LINE="latitude = ${WDX_LAT:-}"; [ -n "${WDX_LAT:-}" ] || LAT_LINE="# latitude = "
  LON_LINE="longitude = ${WDX_LON:-}"; [ -n "${WDX_LON:-}" ] || LON_LINE="# longitude = "
  sudo tee "$CONF" >/dev/null <<INI
[agent]
endpoint = $BASE/api/v1/events
api_key = $KEY
source = $SOURCE
path = $DBPATH
station_name = $NAME
device_id = ${WDX_DEVICE_ID:-}
${WDX_STATION_ID:+station_id = $WDX_STATION_ID}
${WDX_START_AFTER:+start_after = $WDX_START_AFTER}
# Used only when the detection database has no coordinates.
$LAT_LINE
$LON_LINE
# Coordinates are rounded before leaving this device. 2 decimals is about 1 km; use 1 for about 11 km.
round_coords = 2
min_confidence = 0.7
interval_seconds = 60
state_file = /var/lib/wdx-agent/state.json
INI
  sudo chmod 640 "$CONF"; sudo chown "root:$(id -gn "$RUN_USER")" "$CONF"
else
  say "Updating existing $CONF (key and server address; other settings kept)"
  sudo sed -i "s|^api_key *=.*|api_key = $KEY|; s|^endpoint *=.*wildnetwork\.arunrajiah\.com.*|endpoint = $BASE/api/v1/events|" "$CONF"
  if [ -n "${WDX_DEVICE_ID:-}" ]; then
    if sudo grep -q "^device_id *=" "$CONF"; then sudo sed -i "s|^device_id *=.*|device_id = $WDX_DEVICE_ID|" "$CONF"
    else echo "device_id = $WDX_DEVICE_ID" | sudo tee -a "$CONF" >/dev/null; fi
  fi
  for kv in "station_id=${WDX_STATION_ID:-}" "start_after=${WDX_START_AFTER:-}"; do
    k="${kv%%=*}"; v="${kv#*=}"; [ -n "$v" ] || continue
    if sudo grep -q "^$k *=" "$CONF"; then sudo sed -i "s|^$k *=.*|$k = $v|" "$CONF"
    else echo "$k = $v" | sudo tee -a "$CONF" >/dev/null; fi
  done
fi
sudo mkdir -p /var/lib/wdx-agent && sudo chown "$RUN_USER" /var/lib/wdx-agent

# Check before starting the service, so a wrong path or key shows up now and not silently in the system log.
say "Checking that the detections can be read"
if ! sudo -u "$RUN_USER" python3 /opt/wdx-agent/wdx_agent.py --config "$CONF" --dry-run >/tmp/wdx-agent-check.log 2>&1; then
  echo "Could not read the detections at $DBPATH:"; tail -3 /tmp/wdx-agent-check.log
  echo "Fix the path (WDX_PATH=...) and run the installer again."; exit 1
fi
say "Checking the key with $BASE"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/events" -H "authorization: Bearer $KEY" -H 'content-type: application/json' -d '[]' || echo 000)"
case "$CODE" in
  401|403) echo "The server did not accept this key. Make a new one in BirdEcho or at $BASE/contribute and run the installer again with WDX_KEY=<key>."; exit 1 ;;
  000) echo "Could not reach $BASE. Check the internet connection; the agent will keep trying once it is running." ;;
esac

# The service file is only written when it is missing or is our own stock file (the only past change was the name in
# Description). A file set up by something else, such as a Wildlife Network Base, has extra settings and is kept.
UNIT=/etc/systemd/system/wdx-agent.service
NEW_UNIT="$(curl -fsSL "$BASE/agent/wdx-agent.service" | sed "s/__USER__/$RUN_USER/")"
stock() { sed 's/^Description=wdx-agent: push wildlife detections to WildNetwork$/Description=wdx-agent: push wildlife detections to Wildlife Network/' "$1"; }
if [ ! -f "$UNIT" ] || [ "$(stock "$UNIT")" = "$NEW_UNIT" ]; then
  printf '%s\n' "$NEW_UNIT" | sudo tee "$UNIT" >/dev/null
else
  say "Keeping the existing $UNIT (it has settings of its own)"
fi
sudo systemctl daemon-reload
# enable --now does not restart a service that is already running, so an update would leave the old version running.
# Clear a failed state (or systemd's start limit after several quick restarts) so the start below is not refused.
sudo systemctl reset-failed wdx-agent >/dev/null 2>&1 || true
if systemctl is-active --quiet wdx-agent; then
  say "Restarting wdx-agent to run the new version"
  sudo systemctl enable wdx-agent >/dev/null 2>&1 || true
  sudo systemctl restart wdx-agent
else
  sudo systemctl enable --now wdx-agent
fi

# The agent logs its version when it starts; read it from this run of the service only, giving it a few seconds.
RUNNING=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  INV="$(systemctl show -p InvocationID --value wdx-agent 2>/dev/null)"
  [ -n "$INV" ] && RUNNING="$(sudo journalctl "_SYSTEMD_INVOCATION_ID=$INV" -o cat --no-pager 2>/dev/null | sed -n 's/^.*wdx-agent \([0-9][0-9.]*\) source=.*$/\1/p' | tail -1)"
  [ -n "$RUNNING" ] && break
  sleep 1
done
INSTALLED="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' /opt/wdx-agent/wdx_agent.py | head -1)"
if [ -n "$RUNNING" ]; then
  say "wdx-agent $RUNNING is running"
  [ "$RUNNING" = "$INSTALLED" ] || echo "Warning: $INSTALLED is installed but $RUNNING is running. Run: sudo systemctl restart wdx-agent"
elif systemctl is-active --quiet wdx-agent; then
  say "wdx-agent $INSTALLED installed and running"
else
  echo "wdx-agent $INSTALLED is installed but the service is not running yet. Check: systemctl status wdx-agent"
fi

say "Done. Your detections will appear on $BASE within a minute or two."
echo "Logs:   journalctl -u wdx-agent -f"
echo "Config: $CONF"

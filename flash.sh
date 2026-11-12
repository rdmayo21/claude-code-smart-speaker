#!/bin/sh
# Flash the Voice PE: USB-C the first time, over Wi-Fi afterwards.
# Run it on the computer that runs bridge.py. Usage: ./flash.sh [ota] [room]
set -eu
cd "$(dirname "$0")"
MODE="usb"; ROOM="bedroom"
for arg in "$@"; do
  case "$arg" in ota) MODE="ota" ;; *) ROOM="$arg" ;; esac
done

[ -f firmware/secrets.yaml ] || { echo "firmware/secrets.yaml missing: copy firmware/secrets.yaml.example first" >&2; exit 1; }

if grep -q '__ASK_AT_FLASH__' firmware/secrets.yaml; then
  printf "Wi-Fi password (2.4 GHz network): "
  stty -echo; read -r PW; stty echo; echo
  [ -n "$PW" ] || { echo "empty password, stopping" >&2; exit 1; }
  PW="$PW" python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path("firmware/secrets.yaml")
p.write_text(p.read_text().replace('"__ASK_AT_FLASH__"', json.dumps(os.environ["PW"])))
PY
  chmod 600 firmware/secrets.yaml
fi

# The speaker's address on the bridge, secret token included. Never printed.
URL=$(uv run -q bridge.py --print-url "$ROOM")
[ -n "$URL" ] || { echo "bridge.py gave no address, stopping" >&2; exit 1; }

cd firmware
if [ "$MODE" = ota ]; then
  exec uvx "esphome==${ESPHOME_VERSION:-2026.10.1}" -s bridge_url "$URL" run speaker.yaml --device voice-dot.local --no-logs
fi
exec uvx "esphome==${ESPHOME_VERSION:-2026.10.1}" -s bridge_url "$URL" run speaker.yaml

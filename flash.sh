#!/bin/sh
# Flash the Voice PE: USB-C the first time, over Wi-Fi afterwards.
# Run it on the computer that runs bridge.py.
#   ./flash.sh [room]        first time, over USB-C (room defaults to bedroom)
#   ./flash.sh ota [room]    later, over Wi-Fi
# Typed the Wi-Fi password wrong? uv run bridge.py --make-secrets "your Wi-Fi name", then flash again.
set -eu
cd "$(dirname "$0")"
MODE="usb"; ROOM="bedroom"
for arg in "$@"; do
  case "$arg" in ota) MODE="ota" ;; *) ROOM="$arg" ;; esac
done

[ -f firmware/secrets.yaml ] || { echo 'firmware/secrets.yaml missing: run  uv run bridge.py --make-secrets "your Wi-Fi name"' >&2; exit 1; }
chmod 600 firmware/secrets.yaml

if grep -q '__ASK_AT_FLASH__' firmware/secrets.yaml; then
  printf "Wi-Fi password (2.4 GHz network): "
  trap 'stty echo' EXIT INT TERM
  stty -echo; read -r PW; stty echo; echo
  trap - EXIT INT TERM
  [ -n "$PW" ] || { echo "empty password, stopping" >&2; exit 1; }
  PW="$PW" uv run -q --no-project python - <<'PY'
import json, os, pathlib
p = pathlib.Path("firmware/secrets.yaml")
p.write_text(p.read_text().replace('"__ASK_AT_FLASH__"', json.dumps(os.environ["PW"])))
PY
fi

# This speaker's address on the bridge, secret token included. Never printed.
URL=$(uv run -q bridge.py --port "${BRIDGE_PORT:-7697}" --print-url "$ROOM")
[ -n "$URL" ] || { echo "bridge.py gave no address, stopping" >&2; exit 1; }

cd firmware
echo "Compiling and flashing. When the device log starts scrolling it is done: press Ctrl-C."
if [ "$MODE" = ota ]; then
  exec uvx "esphome==${ESPHOME_VERSION:-2026.10.1}" -s bridge_url "$URL" run speaker.yaml --device voice-dot.local --no-logs
fi
exec uvx "esphome==${ESPHOME_VERSION:-2026.10.1}" -s bridge_url "$URL" run speaker.yaml

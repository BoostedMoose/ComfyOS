#!/usr/bin/env bash
# Start ComfyOS (Linux/macOS). Reachable in the home network at http://<machine>:8000
# On the Raspberry Pi better use install_pi.sh (autostart, name "comfyos", port 80).
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
HOST="${COMFYOS_HOST:-0.0.0.0}"
PORT="${COMFYOS_PORT:-8000}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.10 oder neuer wurde nicht gefunden. Bitte installieren und erneut starten."
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install -q -r backend/requirements.txt
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "  ComfyOS startet:  http://127.0.0.1:$PORT"
[ -n "$IP" ] && echo "  Im Heimnetz:     http://$IP:$PORT"
echo
set +e
while true; do
  COMFYOS_HOST="$HOST" COMFYOS_PORT="$PORT" .venv/bin/python backend/run.py
  code=$?
  if [ "$code" -eq 3 ]; then
    echo "  Neustart (Update) ..."
    .venv/bin/python -m pip install -q -r backend/requirements.txt
    continue
  fi
  exit "$code"
done

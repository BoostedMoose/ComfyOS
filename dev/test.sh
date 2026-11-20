#!/usr/bin/env bash
# ComfyOS – run the backend tests (on the Pi; see dev/README.md)
# Usage (from the repo root or from dev/):
#   bash dev/test.sh                                   # all backend tests
#   bash dev/test.sh --only test_weather,test_privacy  # single tests
# The Playwright UI tests run on Windows, never on the Pi (too heavy for the dev board).

set -euo pipefail

for arg in "$@"; do
  if [[ "$arg" == "--ui" || "$arg" == "--ui-only" || "$arg" == *ui_* ]]; then
    echo "UI tests (Playwright) do not run on the Pi. Start them on Windows (Git Bash, in the project folder):"
    echo "  PYTHONIOENCODING=utf-8 ~/.venvs/homeos-test/Scripts/python.exe tests/run_all.py --ui-only"
    exit 2
  fi
done

PYTHON="${COMFYOS_TEST_PYTHON:-$HOME/.venvs/comfyos-test/bin/python}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ ! -x "$PYTHON" ]]; then
  echo "Test-venv nicht gefunden: $PYTHON"
  echo "Neu anlegen:"
  echo "  python3 -m venv ~/.venvs/comfyos-test"
  echo "  ~/.venvs/comfyos-test/bin/python -m pip install -r backend/requirements.txt pytest"
  exit 1
fi

export PYTHONIOENCODING=utf-8
exec "$PYTHON" "$ROOT/tests/run_all.py" "$@"

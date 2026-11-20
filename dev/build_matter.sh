#!/usr/bin/env bash
# ComfyOS – bundle the helper program of the Matter plugin (on the Pi; needs Node and npm, see dev/README.md)
# Usage (from the repo root or from dev/):
#   bash dev/build_matter.sh            # fetch dependencies exactly by package-lock.json, bundle, npm audit
#   bash dev/build_matter.sh --update   # resolve package-lock.json anew from package.json first (after a version change)
#
# Result: public/plugins/matter/bridge/dist/bridge.mjs – one file with matter.js and the helper program. It is
# checked in and shipped; users need neither npm nor a build step. The run is only needed if
# bridge.mjs/setup.mjs are changed or matter.js is updated (version in package.json, then --update).
# node_modules/ stays on the development machine and is in .gitignore.
#
# Connects to registry.npmjs.org – a development tool, never part of ComfyOS.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRIDGE="$ROOT/public/plugins/matter/bridge"

if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  echo "Node and npm are missing. The bundle public/plugins/matter/bridge/dist/bridge.mjs is in the repo – a build is only"
  echo "needed when the helper program is changed."
  exit 1
fi

cd "$BRIDGE"
if [[ "${1:-}" == "--update" ]]; then
  npm install --no-fund --no-audit        # resolves package.json anew and writes package-lock.json
else
  npm ci --no-fund --no-audit             # exactly the versions from package-lock.json
fi
node build.mjs
node --check dist/bridge.mjs
echo
npm audit --omit=dev || echo "npm audit meldet Funde – vor dem Release bewerten."
echo
echo "Fertig: $(du -h dist/bridge.mjs | cut -f1) public/plugins/matter/bridge/dist/bridge.mjs"
echo "Danach: bash dev/test.sh --only test_matter_node,test_matter_plugin   (mit Node ab Version 24)"

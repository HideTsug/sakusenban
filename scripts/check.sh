#!/bin/bash
# Mechanical invariant gate:
#  1. render.py renders the example without errors (validate() included)
#  2. templates/template.html is exactly the regenerated output (no hand edits, no drift)
#  3. no unresolved placeholders in assets
set -u
cd "$(dirname "$0")/.."

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if ! python3 scripts/render.py examples/board.yaml examples/state.json \
     --stamp "2026-08-20 00:00" --today 2026-08-20 > "$TMP"; then
  echo "FAIL: render.py exited non-zero" >&2
  exit 1
fi

if ! diff -u templates/template.html "$TMP"; then
  echo "FAIL: templates/template.html is stale. Regenerate:" >&2
  echo '  python3 scripts/render.py examples/board.yaml examples/state.json --stamp "2026-08-20 00:00" --today 2026-08-20 > templates/template.html' >&2
  exit 1
fi

for ph in __BOARD_KEY__ __REPORT_HEAD__; do
  if ! grep -q "$ph" assets/board.js; then
    echo "FAIL: assets/board.js lost placeholder $ph" >&2
    exit 1
  fi
done

echo "OK: render + template freshness + placeholders"

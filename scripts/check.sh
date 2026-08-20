#!/bin/bash
# Mechanical invariant gate:
#  1. render.py renders the example without errors (validate() included)
#  2. both templates are exactly the regenerated outputs (no hand edits, no drift)
#  3. each output mode has the required document structure
#  4. no unresolved placeholders in assets
set -u
cd "$(dirname "$0")/.."

TMP_TEMPLATE="$(mktemp /tmp/sakusenban-template.XXXXXX)"
TMP_FRAGMENT="$(mktemp /tmp/sakusenban-fragment.XXXXXX)"
trap 'rm -f "$TMP_TEMPLATE" "$TMP_FRAGMENT"' EXIT

if ! python3 scripts/render.py examples/board.yaml examples/state.json \
     --stamp "2026-08-20 00:00" --today 2026-08-20 > "$TMP_TEMPLATE"; then
  echo "FAIL: render.py exited non-zero for document output" >&2
  exit 1
fi

if ! python3 scripts/render.py examples/board.yaml examples/state.json --fragment \
     --stamp "2026-08-20 00:00" --today 2026-08-20 > "$TMP_FRAGMENT"; then
  echo "FAIL: render.py exited non-zero for fragment output" >&2
  exit 1
fi

if ! diff -u templates/template.html "$TMP_TEMPLATE"; then
  echo "FAIL: templates/template.html is stale. Regenerate:" >&2
  echo '  python3 scripts/render.py examples/board.yaml examples/state.json --stamp "2026-08-20 00:00" --today 2026-08-20 > templates/template.html' >&2
  exit 1
fi

if ! diff -u templates/fragment.html "$TMP_FRAGMENT"; then
  echo "FAIL: templates/fragment.html is stale. Regenerate:" >&2
  echo '  python3 scripts/render.py examples/board.yaml examples/state.json --fragment --stamp "2026-08-20 00:00" --today 2026-08-20 > templates/fragment.html' >&2
  exit 1
fi

if ! python3 - templates/fragment.html templates/template.html <<'PY'
import pathlib
import re
import sys

fragment_path = pathlib.Path(sys.argv[1])
template_path = pathlib.Path(sys.argv[2])
fragment = fragment_path.read_bytes()
forbidden = {
    "<!doctype": rb"<!doctype\b",
    "<html": rb"<html\b",
    "<head": rb"<head\b",
    "<body": rb"<body\b",
    "<meta charset": rb"<meta\b[^>]*\bcharset\s*=",
    '<meta name="viewport"': rb"<meta\b[^>]*\bname\s*=\s*['\"]?viewport(?:['\"\s/>])",
}
found = [label for label, pattern in forbidden.items()
         if re.search(pattern, fragment, flags=re.IGNORECASE)]
if found:
    print(
        "FAIL: templates/fragment.html contains forbidden document scaffolding: "
        + ", ".join(found),
        file=sys.stderr,
    )
    raise SystemExit(1)

meta_position = template_path.read_bytes().lower().find(b"<meta charset")
if meta_position < 0:
    print("FAIL: templates/template.html has no <meta charset", file=sys.stderr)
    raise SystemExit(1)
if meta_position >= 1024:
    print(
        f"FAIL: templates/template.html <meta charset is at byte {meta_position}; "
        "it must be before byte 1024",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
then
  exit 1
fi

for ph in __BOARD_KEY__ __REPORT_HEAD__; do
  if ! grep -q "$ph" assets/board.js; then
    echo "FAIL: assets/board.js lost placeholder $ph" >&2
    exit 1
  fi
done

echo "OK: render + template freshness + output modes + placeholders"

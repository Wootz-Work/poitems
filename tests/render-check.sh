#!/bin/bash
# Loads the page in headless Chrome and asserts Handsontable actually rendered.
# Catches errors thrown out of the renderer, which leave a blank table and which
# the headless logic suites cannot see.
#
# Usage: ./tests/render-check.sh [path-to-index.html]
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$D/../frontend/index.html}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
[ -x "$CHROME" ] || { echo "no chromium browser found"; exit 2; }

STAGE="$(mktemp -d)"
mkdir -p "$STAGE/frontend"
python3 - "$SRC" "$STAGE/frontend/index.html" <<'PYEOF'
import sys
src, dst = sys.argv[1], sys.argv[2]
html = open(src, encoding='utf-8').read()
trap = """<body>
<script>
window.__errs = [];
window.addEventListener('error', function (e) {
  window.__errs.push(String(e.message));
  document.title = 'RENDER_ERROR :: ' + window.__errs.join(' | ');
});
window.addEventListener('unhandledrejection', function (e) {
  window.__errs.push('unhandled rejection: ' + String(e.reason && e.reason.message || e.reason));
  document.title = 'RENDER_ERROR :: ' + window.__errs.join(' | ');
});
</script>"""
assert html.count('<body>') == 1, 'expected exactly one <body> tag'
open(dst, 'w', encoding='utf-8').write(html.replace('<body>', trap, 1))
PYEOF
cp "$D/../frontend/style.css" "$STAGE/frontend/" 2>/dev/null || true
cp "$D/../frontend/sample-screenshot.png" "$STAGE/frontend/" 2>/dev/null || true

PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
( cd "$STAGE" && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 ) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null; rm -rf "$STAGE"' EXIT

for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/frontend/index.html" >/dev/null 2>&1; then break; fi
  sleep 0.25
done
if ! curl -fsS "http://127.0.0.1:$PORT/frontend/index.html" >/dev/null 2>&1; then
  echo "FAIL: local server never came up - result would be meaningless"; exit 2
fi

URL="http://127.0.0.1:$PORT/frontend/index.html?project=RenderTest&part=RT-1&parentdrgnum=RT-P&rowID=RT-ROW&item=0"
DOM="$("$CHROME" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=9000 \
        --dump-dom "$URL" 2>/dev/null)"

TITLE=$(printf '%s' "$DOM" | grep -o '<title>[^<]*</title>' | head -1)
ERRS=$(printf '%s' "$TITLE" | grep -o 'RENDER_ERROR :: .*' | sed 's|</title>||')
CELLS=$(printf '%s' "$DOM" | grep -o '<td' | wc -l | tr -d ' ')
ROWS=$(printf '%s' "$DOM" | grep -o 'class="[^"]*handsontable' | wc -l | tr -d ' ')
BTN=$(printf '%s' "$DOM" | grep -c 'id="submit-btn"' | tr -d ' ')

echo "  javascript errors     : ${ERRS:-none}"
echo "  submit button present : $BTN"
echo "  handsontable elements : $ROWS"
echo "  rendered <td> cells   : $CELLS"

if [ "$BTN" -lt 1 ]; then echo "FAIL: page did not load at all"; exit 1; fi
if [ -n "$ERRS" ]; then
  echo "FAIL: javascript threw during load"
  exit 1
fi
# 5 visible columns x 5 seed rows, plus the spare row Handsontable adds.
if [ "$CELLS" -lt 20 ]; then
  echo "FAIL: only $CELLS cells rendered, expected 25+ — rendering aborted partway"
  exit 1
fi
echo "PASS: table rendered cleanly"

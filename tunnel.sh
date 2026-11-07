#!/usr/bin/env bash
# Optional foreground tunnel. Never installs cloudflared or starts by default.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [ "${1:-}" != quick ] || [ "$#" != 1 ]; then
  echo 'Usage: ./tunnel.sh quick (explicitly exposes your running panel via HTTPS)'
  echo 'Stop with Ctrl+C. Install cloudflared yourself from official Cloudflare docs.'
  echo 'Named tunnels: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/'
  exit 2
fi
command -v cloudflared >/dev/null || {
  echo 'cloudflared missing. Install manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/' >&2
  exit 1
}
[ -x .venv/bin/python ] && [ -f .env ] && [ ! -L .env ] || {
  echo 'Run bash install.sh and ./run.sh first.' >&2; exit 1;
}
PORT="$(.venv/bin/python - <<'PY'
import os
from dotenv import load_dotenv
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
load_dotenv('.env')
token = os.environ.get('PANEL_TOKEN', '').strip()
if not token:
    raise SystemExit('PANEL_TOKEN is required; refusing public exposure.')
if os.environ.get('HOST', '127.0.0.1') not in ('127.0.0.1', 'localhost', '::1'):
    raise SystemExit('Set HOST=127.0.0.1 and restart the server before tunneling.')
try:
    port = int(os.environ.get('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError
except ValueError:
    raise SystemExit('Invalid PORT.')
base = f'http://127.0.0.1:{port}'
try:
    for path in ('/', '/api/health', '/v1/models'):
        try:
            with urlopen(base + path, timeout=5):
                raise SystemExit('Unauthenticated access succeeded; refusing public exposure.')
        except HTTPError as exc:
            if exc.code != 401:
                raise SystemExit('Expected authentication challenge; refusing public exposure.')
            if path == '/' and not exc.headers.get('WWW-Authenticate', '').lower().startswith('basic'):
                raise SystemExit('Browser Basic authentication is required.')
    req = Request(base + '/api/health', headers={'Authorization': 'Bearer ' + token})
    with urlopen(req, timeout=5) as response:
        if response.status != 200:
            raise SystemExit('Authenticated health check failed.')
except (HTTPError, URLError, OSError):
    raise SystemExit('Cannot verify local authenticated server; start ./run.sh first.')
print(port)
PY
)"
echo 'Exposing one shared-history panel. Use ONLY the HTTPS URL printed by cloudflared.' >&2
echo 'Login: panel / your local PANEL_TOKEN. Anyone with it can read/delete all history.' >&2
echo 'Ctrl+C stops this foreground tunnel; the panel stays local.' >&2
exec cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT"

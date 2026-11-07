#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")"
[ -x .venv/bin/python ] || { echo 'Run bash install.sh first.' >&2; exit 1; }
[ -f .env ] && [ ! -L .env ] || { echo 'A regular private .env is required; run bash install.sh.' >&2; exit 1; }
chmod 600 .env
exec .venv/bin/python - <<'PY'
import os
import sys
from dotenv import load_dotenv
load_dotenv('.env', override=False)
if not os.environ.get('PANEL_TOKEN', '').strip():
    raise SystemExit('PANEL_TOKEN is required. Set it in your private .env; refusing to start.')
host = os.environ.get('HOST', '127.0.0.1')
try:
    port = int(os.environ.get('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError
except ValueError:
    raise SystemExit('PORT must be an integer between 1 and 65535.')
print(f'NoTrack Panel: http://{host}:{port}/ (one operator; keep private)', flush=True)
os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'app:app',
                        '--env-file', '.env', '--host', host, '--port', str(port),
                        '--workers', '1', '--log-level', 'info'])
PY

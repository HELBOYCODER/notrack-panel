#!/usr/bin/env bash
# NoTrack Panel launcher
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "[*] installing dependencies…"
  python3 -m pip install -q -r requirements.txt
fi

echo "════════════════════════════════════════"
echo "  NoTrack Panel"
echo "  panel : http://localhost:${PORT}/"
echo "  api   : http://localhost:${PORT}/v1"
echo "════════════════════════════════════════"

exec python3 -m uvicorn app:app --host "$HOST" --port "$PORT" --log-level info
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  NoTrack Panel — one-command install
#  Creates a venv, installs deps, writes a systemd unit (if possible).
# ─────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PORT="${PORT:-8080}"
SERVICE=notrack-panel

say() { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
say "Python $PYV detected"

# ── venv module can be missing on slim images ─────────────────
if ! python3 -c 'import venv,ensurepip' 2>/dev/null; then
  warn "python3-venv missing — trying to install"
  if command -v apt-get >/dev/null && sudo -n true 2>/dev/null; then
    sudo -n apt-get update -qq && sudo -n apt-get install -y -qq "python${PYV}-venv" python3-venv || true
  fi
fi

if [ ! -d .venv ]; then
  say "creating virtualenv"
  python3 -m venv .venv
fi

say "installing dependencies"
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

mkdir -p data

# ── systemd, when we have the rights ─────────────────────────
if command -v systemctl >/dev/null && sudo -n true 2>/dev/null; then
  say "installing systemd service ($SERVICE)"
  sudo -n tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=NoTrack Panel — uncensored model gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
Environment=PANEL_PACE=1.9
ExecStart=$DIR/.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port $PORT --log-level info
Restart=always
RestartSec=4
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE

[Install]
WantedBy=multi-user.target
EOF
  sudo -n systemctl daemon-reload
  sudo -n systemctl enable --now $SERVICE
  say "service started"
  echo
  echo "  panel : http://<this-host>:$PORT/"
  echo "  api   : http://<this-host>:$PORT/v1"
  echo "  logs  : sudo journalctl -u $SERVICE -f"
  echo "  stop  : sudo systemctl stop $SERVICE"
else
  warn "no passwordless sudo — running in foreground instead"
  echo
  exec ./.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --log-level info
fi
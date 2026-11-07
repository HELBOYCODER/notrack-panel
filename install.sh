#!/usr/bin/env bash
# Linux/macOS installer. No OS packages, sudo, or service on the default path.
set -euo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")"
DIR="$(pwd -P)"
SERVICE=0
case "${1:-}" in
  '') ;;
  --service) SERVICE=1 ;;
  -h|--help) echo 'Usage: bash install.sh [--service]'; exit 0 ;;
  *) echo 'Usage: bash install.sh [--service]' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo 'Too many arguments.' >&2; exit 2; }
case "$(uname -s)" in
  Linux|Darwin) ;;
  *) echo 'Requires Linux/macOS. On Windows use the manual README steps.' >&2; exit 1 ;;
esac
command -v python3 >/dev/null || { echo 'Install Python >=3.10 first.' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python >=3.10 required")'
if [ "$SERVICE" = 1 ]; then
  [ "$(uname -s)" = Linux ] && [ "$(id -u)" != 0 ] || {
    echo '--service requires Linux and the current non-root user.' >&2; exit 1;
  }
  command -v systemctl >/dev/null || { echo 'systemd is not installed.' >&2; exit 1; }
  systemctl --user show-environment >/dev/null || {
    echo 'A working systemd user manager is required; use ./run.sh instead.' >&2; exit 1;
  }
  case "$DIR" in
    *[\"\\%]*|*$'\n'*|*$'\r'*) echo 'Service path contains unsupported characters.' >&2; exit 1 ;;
  esac
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  echo 'Install Python venv/ensurepip with your OS package manager, then retry.' >&2
  exit 1
fi
python3 - <<'PY'
import os
import secrets
from pathlib import Path
p = Path('.env')
if p.is_symlink():
    raise SystemExit('Refusing a symlink .env.')
if not p.exists():
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('PANEL_TOKEN=' + secrets.token_urlsafe(32) + '\n'
                'HOST=127.0.0.1\nPORT=8080\nPANEL_DATA=./data\nPANEL_PACE=5.0\n')
    print('Created private .env with a random token (not displayed).')
else:
    print('Preserving existing .env and token.')
p.chmod(0o600)
Path('data').mkdir(mode=0o700, exist_ok=True)
PY
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python - <<'PY'
from dotenv import dotenv_values
if not (dotenv_values('.env').get('PANEL_TOKEN') or '').strip():
    raise SystemExit('PANEL_TOKEN is empty in .env; set a strong random token locally before running.')
PY
chmod +x run.sh tunnel.sh
if [ "$SERVICE" = 1 ]; then
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/notrack-panel.service" <<EOF
[Unit]
Description=NoTrack Panel (single operator)
After=network.target

[Service]
Type=simple
WorkingDirectory="$DIR"
ExecStart="$DIR/run.sh"
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true
UMask=0077
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now notrack-panel.service
  echo 'User service started. Logs: journalctl --user -u notrack-panel -f'
else
  echo 'Installed. Start explicitly: ./run.sh'
fi
echo 'Read PANEL_TOKEN only in your local .env editor; do not publish or paste it into logs.'
echo 'Browser login: username panel; password is PANEL_TOKEN.'

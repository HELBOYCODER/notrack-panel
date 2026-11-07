#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Expose the panel to the internet without opening a port.
#
#  Most VPS providers (AWS, GCP, Oracle, Hetzner) firewall everything
#  except 22/80/443 by default. Instead of fighting that, this tunnels
#  outbound with Cloudflare — no inbound port, no security-group edit,
#  and you get HTTPS for free.
#
#    ./tunnel.sh quick      throwaway *.trycloudflare.com URL (no account)
#    ./tunnel.sh named      permanent URL on your own Cloudflare domain
#    ./tunnel.sh stop       kill the tunnel
# ─────────────────────────────────────────────────────────────
set -euo pipefail

PORT="${PORT:-8080}"
LOG="${TMPDIR:-/tmp}/notrack-tunnel.log"
PIDFILE="${TMPDIR:-/tmp}/notrack-tunnel.pid"

say() { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }

need_cloudflared() {
  command -v cloudflared >/dev/null && return 0
  err "cloudflared not installed"
  cat <<'EOF'

  Install it:

    # Debian / Ubuntu (amd64)
    curl -L -o /tmp/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared

    # macOS
    brew install cloudflared

EOF
  exit 1
}

case "${1:-quick}" in
  quick)
    need_cloudflared
    say "starting quick tunnel on :$PORT"
    : > "$LOG"
    nohup cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate \
      > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"

    URL=""
    for _ in $(seq 1 30); do
      URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)
      [ -n "$URL" ] && break
      sleep 1
    done

    if [ -z "$URL" ]; then
      err "no URL after 30s — see $LOG"
      tail -20 "$LOG" || true
      exit 1
    fi

    echo
    echo "  ═══════════════════════════════════════════════"
    echo "   panel : $URL/"
    echo "   api   : $URL/v1"
    echo "  ═══════════════════════════════════════════════"
    echo
    echo "  This URL lives until the process dies. For a"
    echo "  permanent one: ./tunnel.sh named"
    ;;

  named)
    need_cloudflared
    HOSTNAME_="${2:-}"
    if [ -z "$HOSTNAME_" ]; then
      echo "usage: ./tunnel.sh named panel.example.com"
      echo
      echo "one-time setup:"
      echo "  cloudflared tunnel login"
      echo "  cloudflared tunnel create notrack"
      exit 1
    fi
    say "routing $HOSTNAME_ -> http://127.0.0.1:$PORT"
    cloudflared tunnel --url "http://127.0.0.1:$PORT" \
      --hostname "$HOSTNAME_" --no-autoupdate
    ;;

  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
      say "tunnel stopped"
    else
      pkill -f "cloudflared tunnel" 2>/dev/null && say "tunnel stopped" || echo "no tunnel running"
    fi
    ;;

  *)
    echo "usage: ./tunnel.sh [quick|named <hostname>|stop]"
    exit 1
    ;;
esac
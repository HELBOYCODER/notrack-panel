#!/usr/bin/env bash
# Local uncensored model: builds llama.cpp server, downloads a hash-verified GGUF,
# creates a private API key. Optional: --service installs a systemd unit (needs sudo).
#   bash local-llm/setup.sh            # build + download + key, no service
#   bash local-llm/setup.sh --service  # also install & start systemd service
set -euo pipefail
umask 077

SERVICE=0
case "${1:-}" in
  '') ;;
  --service) SERVICE=1 ;;
  -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
  *) echo "usage: bash local-llm/setup.sh [--service]" >&2; exit 2 ;;
esac

BASE="${LOCAL_LLM_DIR:-$HOME/local-llm}"
PORT="${LOCAL_LLM_PORT:-8081}"
THREADS="${LOCAL_LLM_THREADS:-$(nproc)}"
CTX="${LOCAL_LLM_CTX:-8192}"
# Default: Qwen2.5-3B abliterated, Q4_K_M (~1.9 GB, needs ~3 GB RAM). Override all three to change.
MODEL="${LOCAL_LLM_MODEL:-Qwen2.5-3B-Instruct-abliterated.Q4_K_M.gguf}"
REPO="${LOCAL_LLM_REPO:-mradermacher/Qwen2.5-3B-Instruct-abliterated-GGUF}"
SHA="${LOCAL_LLM_SHA256:-c771d34ef5ad81f3a2ffb44a747c61de35b7ea2cc1fa522d9613e872a6dfe9eb}"

for c in git cmake make g++ curl sha256sum; do
  command -v "$c" >/dev/null || { echo "missing: $c (install build tools first)" >&2; exit 1; }
done

mkdir -p "$BASE/models"
cd "$BASE"
echo "▸ directory: $BASE"

if ! echo "$SHA  models/$MODEL" | sha256sum -c --status 2>/dev/null; then
  echo "▸ downloading $MODEL"
  curl -fL --retry 5 -C - -o "models/$MODEL" "https://huggingface.co/$REPO/resolve/main/$MODEL"
fi
echo "$SHA  models/$MODEL" | sha256sum -c || { echo "hash mismatch; delete models/$MODEL and retry" >&2; exit 1; }

if [ ! -x llama.cpp/build/bin/llama-server ]; then
  echo "▸ building llama.cpp (CPU, takes several minutes)"
  [ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp
  cmake -S llama.cpp -B llama.cpp/build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON \
    -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  cmake --build llama.cpp/build --target llama-server -j "$THREADS"
fi

if [ ! -s api-key ]; then
  python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > api-key
  echo "▸ created private api-key (not printed; read it locally: cat $BASE/api-key)"
fi
chmod 600 api-key

CMD="$BASE/llama.cpp/build/bin/llama-server -m $BASE/models/$MODEL --host 127.0.0.1 --port $PORT --alias local-uncensored -c $CTX -t $THREADS -np 1 --jinja --api-key-file $BASE/api-key"

if [ "$SERVICE" = 1 ]; then
  sudo tee /etc/systemd/system/local-llm.service >/dev/null <<EOF
[Unit]
Description=Local uncensored LLM (llama.cpp)
After=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BASE
ExecStart=$CMD
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now local-llm
  echo "▸ service started; first load takes 1–2 min. logs: sudo journalctl -u local-llm -f"
else
  echo "▸ start manually:"
  echo "  $CMD"
fi
echo "▸ API (loopback): http://127.0.0.1:$PORT/v1   model: local-uncensored"
echo "▸ test: bash local-llm/test.sh"

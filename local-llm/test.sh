#!/usr/bin/env bash
# Smoke test: auth required, Persian answer, tool calling. Never prints the key.
set -euo pipefail
BASE="${LOCAL_LLM_DIR:-$HOME/local-llm}"
URL="${LOCAL_LLM_URL:-http://127.0.0.1:${LOCAL_LLM_PORT:-8081}}/v1"
KEY="$(cat "$BASE/api-key")"

echo -n "no-key status (expect 401): "
curl -s -o /dev/null -w '%{http_code}\n' "$URL/models"

ask() {
  curl -s -m 300 "$URL/chat/completions" -H "Authorization: Bearer $KEY" \
    -H 'Content-Type: application/json' -d "$1" |
    python3 -c 'import sys,json;m=json.load(sys.stdin)["choices"][0]["message"];print(m.get("content") or "", m.get("tool_calls") or "")'
}

echo "▸ Persian:"
ask '{"model":"local-uncensored","max_completion_tokens":120,"messages":[{"role":"user","content":"به فارسی در یک جمله: پایتخت ایران کجاست؟"}]}'

echo "▸ tool call (expect shell_execute):"
ask '{"model":"local-uncensored","max_completion_tokens":200,"stream_options":{"include_usage":true},"tools":[{"type":"function","function":{"name":"shell_execute","description":"Run a shell command","parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}],"messages":[{"role":"user","content":"List files in /tmp using the shell tool."}]}'

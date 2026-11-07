# Local uncensored model (self-hosted)

A fully free, quota-free alternative to web backends: run an abliterated open model on your own server with [llama.cpp](https://github.com/ggml-org/llama.cpp). It exposes an OpenAI-compatible API **with real tool calling**, so it works in Minis agent chat (unlike the text-only NoTrack web adapter).

## Install

```bash
git clone https://github.com/HELBOYCODER/notrack-panel
cd notrack-panel
bash local-llm/setup.sh --service   # build + download + private key + systemd
bash local-llm/test.sh              # auth, Persian, tool-call check
```

Default model: `mradermacher/Qwen2.5-3B-Instruct-abliterated-GGUF`, file `Q4_K_M` (~1.9 GB), SHA-256 verified. Override with `LOCAL_LLM_MODEL`, `LOCAL_LLM_REPO`, `LOCAL_LLM_SHA256` (all three together). Other knobs: `LOCAL_LLM_PORT` (8081), `LOCAL_LLM_THREADS`, `LOCAL_LLM_CTX` (8192), `LOCAL_LLM_DIR` (`~/local-llm`).

Requirements: Linux, git, cmake, make, g++, curl, python3. ~3 GB free RAM for the default model; add swap on small servers. Without `--service`, the script prints the start command instead of using sudo.

## Security

- Binds to **127.0.0.1 only**. A random API key is created in `~/local-llm/api-key` (mode 600, never printed); every request needs `Authorization: Bearer <key>`.
- For remote access put HTTPS in front (e.g. `cloudflared tunnel --url http://127.0.0.1:8081`). The key still protects it; a quick-tunnel URL is temporary and is not access control.
- If the key leaks: replace the file content and `sudo systemctl restart local-llm`.

## Connect to Minis

Settings → Providers → add **OpenAI-compatible**:
- Base URL: `https://<your-https-host>/v1`, append `/v1`: **off**, Responses API: **off**
- API key: contents of `~/local-llm/api-key`
- Model: `local-uncensored`; set response timeout high (CPU is slow).

## Measured on a 2-vCPU / 3.7 GB RAM server, no GPU

- ~2.5–3.5 tokens/s; a short answer ≈ 10 s. First load after restart ≈ 2 min.
- Persian answers work; the 3B model makes arithmetic/reasoning mistakes (it answered 23×7 wrongly in testing).
- Tool calling verified: returned `shell_execute` with `ls /tmp`.

"Abliterated" removes the refusal direction from weights; it is not a guarantee of any specific output, and output is untrusted. Model license: see the upstream model card.

---

## خلاصهٔ فارسی

مدل بدون سانسور روی سرور خودتان؛ **رایگان، بدون سهمیه و بدون وابستگی به سایت دیگر**. برخلاف NoTrack، **ابزارهای Minis هم کار می‌کنند**.

```bash
bash local-llm/setup.sh --service
bash local-llm/test.sh
```

- فقط روی `127.0.0.1` باز است و با کلید تصادفی در `~/local-llm/api-key` محافظت می‌شود.
- برای دسترسی از بیرون HTTPS (مثلاً cloudflared) جلویش بگذارید.
- در Minis: Provider از نوع OpenAI، آدرس `.../v1`، افزودن `/v1` خاموش، Responses خاموش، مدل `local-uncensored`.
- روی سرور ۲ هسته‌ای بدون GPU: حدود ۳ توکن در ثانیه، جواب کوتاه ≈ ۱۰ ثانیه، بارگذاری اولیه ≈ ۲ دقیقه. مدل ۳ میلیاردی در حساب خطا می‌کند.

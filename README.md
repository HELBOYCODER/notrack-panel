# NoTrack Panel

**0.1.3 — prerelease.** For agent clients (e.g. Minis chat) use model **`notrack-chat`**: it explicitly drops system prompts and tools and sends only the recent conversation (reported in `X-NoTrack-Chat-Mode`); tools are not executed. A single-operator browser panel and a **text-only subset** of the OpenAI chat-completions schema for NoTrack's web-chat backend.

[راهنمای فارسی / Persian quickstart](README.fa.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)

> Independent, unofficial project; not affiliated with NoTrack or OpenAI. The web backend may be undocumented and change without notice. There are no guarantees about availability, model identity, base architecture, version, or unfiltered output. This is **not** the official paid NoTrack API.

## Install (Linux / macOS)

Requires Git, Bash, Python **3.10+**, and Python's `venv`/`ensurepip`. Install missing prerequisites with your preferred OS tools first. The default installer never invokes `apt` or `sudo`, installs no OS packages, creates no service, and does not launch the server.

```bash
git clone https://github.com/HELBOYCODER/notrack-panel
cd notrack-panel
bash install.sh
./run.sh
```

Open <http://127.0.0.1:8080/>. Your browser displays an **HTTP Basic login popup**:

- Username: **`panel`**
- Password: the **`PANEL_TOKEN`** value in your local `.env`

The installer generates a cryptographically random token, writes `.env` with permissions **600**, and never prints the token. Read it only in your local editor, for example `nano .env`; do not paste it into issues, screenshots, logs, or chat. Re-running the installer preserves an existing token. An empty existing token must be replaced locally. Stop the foreground server with **Ctrl+C**.

`run.sh` uses `.venv`, validates the token, loads `.env` with `python-dotenv`, and launches one Uvicorn worker with `--env-file .env`. Default binding is **`HOST=127.0.0.1`, `PORT=8080`**. Environment variables already set in the shell override `.env`. Do not run multiple workers, replicas, or processes against the same data directory.

### Optional systemd user service (Linux only)

Explicit opt-in, run as your **current non-root user**, not with sudo:

```bash
bash install.sh --service
systemctl --user status notrack-panel
journalctl --user -u notrack-panel -f
# Stop and disable:
systemctl --user disable --now notrack-panel
```

Uses the existing systemd user manager; no dedicated account or system-wide unit is created. The unit sets `NoNewPrivileges=true`, `PrivateTmp=true`, and `UMask=0077`; `.env` stays mode 600. User services and `PrivateTmp` require working systemd/user-namespace support. If unavailable, use `./run.sh`. Starting at boot before login may require administrator-enabled user lingering; the installer does not enable it.

### Windows (manual PowerShell)

Use Python 3.10+; the Bash installer is for Linux/macOS. Clone the repository, enter its directory, then:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -c "from pathlib import Path; import secrets; p=Path('.env'); p.exists() and exit('Existing .env preserved; edit it locally'); p.write_text('PANEL_TOKEN='+secrets.token_urlsafe(32)+'\nHOST=127.0.0.1\nPORT=8080\n', encoding='utf-8')"
.\.venv\Scripts\python -m uvicorn app:app --env-file .env --host 127.0.0.1 --port 8080 --workers 1
```

Restrict `.env` and `data/` using Windows file ACLs before running; POSIX mode 600 is not a Windows ACL. Read the token in your local editor and use the same browser login. Missing/empty tokens fail closed.

## Security and data

- **Every `/api/*` and `/v1/*` route requires `PANEL_TOKEN`**, including health and model listings. Missing/empty configuration fails closed rather than exposing an anonymous API.
- Browser access uses Basic auth (`panel` / token). Programmatic API requests use **`Authorization: Bearer <PANEL_TOKEN>`** with that same token, never an arbitrary API key. Keep the token out of URLs.
- This is **one operator with shared history**, not a multi-tenant service. Anyone with the token can access/manage the panel's sessions; there are no per-user boundaries.
- `data/` persists web identity and conversation state **in plaintext**; `.env` and local state are ignored by Git and excluded from Docker builds. They are not encrypted. Secure host access and backups; review staged files before publishing. Upstream also receives your messages and may retain chats.
- Keep loopback binding. For remote access use HTTPS and preferably an additional access-control layer. Basic auth is only base64, not encryption. Never send credentials over public HTTP.

See [SECURITY.md](SECURITY.md) for reporting and operational precautions.

## API: deliberately limited compatibility

Local base URL: `http://127.0.0.1:8080/v1`. Supported routes include `GET /v1/models` and `POST /v1/chat/completions`. Discover model IDs from the authenticated model listing. Example with the optional OpenAI Python client (`pip install openai` in your client environment):

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.environ["PANEL_TOKEN"],  # load your local secret securely
)
first = client.chat.completions.create(
    model="notrack-uncensored",
    messages=[{"role": "user", "content": "My dog is Biscuit."}],
)
print(first.choices[0].message.content)
second = client.chat.completions.create(
    model="notrack-uncensored",
    messages=[{"role": "user", "content": "What is my dog's name?"}],
    extra_body={"session_id": first.session_id},
)
```

The Python client does not read this project's `.env` automatically. Load the token securely in the client process; do not hard-code it. Session IDs are a panel extension, not a standard OpenAI field. For streaming (`stream=True`), session ID is available as the `X-Session-Id` response header and an extra `session_id` field inside standard chat-completion chunks; no unrelated metadata event is injected.

- **Text only**: messages with supported text roles/content, model, streaming, and the panel's `session_id` extension. No images, audio, tool/function calls, or structured-output guarantees.
- Minis' model-test defaults are accepted: `max_completion_tokens` (a strict positive integer up to 1,000,000) and `stream_options: {"include_usage": true|false}`. **The token limit is advisory only** because this web backend cannot enforce it. When supplied, `X-NoTrack-Token-Limit: advisory-only` and a `notrack.warnings` extension disclose that limitation; the warning appears in the first streaming chunk or the non-streaming response. These client options are not forwarded to the web dispatcher.
- `tools`, legacy `max_tokens`, `temperature`, and other unsupported parameters remain **rejected**, not silently accepted. Native function/tool calling and a full Minis Agent session are not supported.
- Upstream supplies no reliable token accounting here. **`usage` is omitted**, even when requested, not fabricated from characters. `X-NoTrack-Usage: unavailable` and the requested-usage warning disclose this. Do not use this gateway for token-based billing.
- **Stateful**: reuse the returned `session_id` and send the next user turn. The server reuses the upstream chat; session IDs do not provide user isolation.
- **Stateless**: omit `session_id` and send history in `messages`. History must fit the conservative instruction budget; **long histories error**. There is no silent truncation. Summarize explicitly or use a stateful session.

### Limits, identity, and model labels

HTTP request bodies are buffered with a **1 MiB (1,048,576 bytes)** hard ceiling, replacing the earlier 64 KiB gate that caused Minis HTTP 413 errors. Set `PANEL_MAX_REQUEST_BYTES` to a lower positive value if needed, then restart; the setting cannot disable or exceed the ceiling. Declared and streamed byte counts are checked before JSON parsing; compressed requests are not accepted. Validation errors do not echo rejected prompts or tool schemas.

**This is a transport limit, not expanded model context or Agent support.** A large tools schema still receives an explicit text-only 422, and an over-budget system/history receives 400. Nothing is silently stripped or truncated. For native tools and larger contexts use a genuine supported API, such as the direct [Venice → Minis setup](docs/VENICE-MINIS.md), not this web adapter.

Conservative local limits informed by observations are **4000 input characters** and about **1980 system/instruction characters** (including folded stateless history). These are character budgets, not token limits or exact upstream contractual limits; upstream may reject smaller requests or change behavior.

One **server-issued persistent web identity** is retained. Requests are **globally serialized** in one server process. There is no UID rotation, identity pool farming, or quota evasion. Upstream rate limits are returned to callers, not bypassed by minting identities; wait before retrying and honor upstream terms. `PANEL_PACE` is a pacing setting, not a throughput guarantee.

Legacy experimental speaker labels **A/B** and aliases containing names such as Minimax or ChatGPT are routing labels only: they do not verify the provider, architecture, or version. **B previously failed**; neither it nor any alternative is promised to work. The `notrack-uncensored` label likewise is not a guarantee of unfiltered output.

## Docker alternative

Requires Docker Engine and the Compose plugin. Container build/runtime has **not been verified in this environment**; no published image is implied. Create a private `.env` without installing the app locally:

```bash
# After cloning and entering the repository:
umask 077
python3 -c 'from pathlib import Path; import secrets; p=Path(".env"); p.exists() and exit("Existing .env preserved; edit locally"); p.write_text("PANEL_TOKEN="+secrets.token_urlsafe(32)+"\nHOST=127.0.0.1\nPORT=8080\n")'
chmod 600 .env
docker compose up --build -d
docker compose logs -f panel
# Stop, retaining history:
docker compose down
```

Compose publishes **127.0.0.1:8080** only. The container binds internally to `0.0.0.0:8080`, runs as **UID/GID 10001**, and persists `/app/data` in the named `panel-data` volume. A new volume inherits the image directory's UID/GID 10001 ownership; do not replace it with a root-owned host bind mount. Existing volumes/bind mounts require ownership preparation for 10001:10001. `docker compose down -v` **deletes stored identity/history** and is not a quota-reset mechanism. Keep a single replica. Compose requires a non-empty `PANEL_TOKEN`; `.env` is not copied into the image. Docker administrators can inspect container secrets.

## Optional HTTPS quick tunnel

Only if you explicitly want internet exposure, manually install [cloudflared from official instructions](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/). Start `./run.sh` locally in one terminal, then in another:

```bash
./tunnel.sh quick
```

The helper checks loopback configuration and authentication challenges before exposing the server; it does not install cloudflared. Use **only the HTTPS URL** it prints, and the same Basic/Bearer credentials. The URL is temporary, not access control. **Ctrl+C** stops the foreground tunnel; the local panel remains running. Cloudflare terminates TLS and is another trust boundary. For persistent named tunnels, follow [official setup documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/); this project does not automate named setup.

## Official API alternative (separate product)

For supported API access, see <https://notrack.ai/api/docs>. The official service uses **`https://api.notrack.ai/v1`**, model **`notrack-uncensored`**, and a **real, separately paid NoTrack API key**. A panel token is not an official key. Web-chat observations and the local conservative limits above do **not** describe the official API's limits, pricing, or guarantees.

## Development

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
node --check static/app.js
node --test tests/test_frontend.cjs
bash -n install.sh run.sh tunnel.sh
```

Use Python from your development virtual environment. CI installs normal pip requirements on Python **3.11 and 3.12**, runs stdlib `unittest` and offline Node frontend tests, and checks JavaScript/shell syntax. A clean install into a temporary Python 3.12 virtualenv, repeat installation preserving `.env`, and local launcher/authentication smoke checks passed during release preparation. The tunnel preflight was exercised with a fake cloudflared command, not a public tunnel. These checks do not prove current upstream model availability, Windows/macOS or systemd operation, or a Docker build.

Configuration: `PANEL_TOKEN` is required; `HOST`/`PORT` default to `127.0.0.1`/`8080`; `PANEL_DATA` defaults to local `data/`; `PANEL_PACE` defaults to `5.0` seconds. Restart after changing configuration. To revoke a compromised token, replace it with a new strong random value in `.env`, update clients, and restart the server/container (including any environment overrides).

## Acknowledgments and license

[HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything) was researched as **inspiration only**. No CLI-Anything generator was run or installed for this project, and no integration is claimed.

MIT © 2026 **HELBOYCODER**. See [LICENSE](LICENSE). Third-party services and dependencies retain their own terms and licenses.

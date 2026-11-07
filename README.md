# NoTrack Panel

A control panel + OpenAI-compatible gateway in front of **notrack.ai**'s
anonymous chat backend. Talk to the uncensored model from a clean UI, manage a
pool of anonymous identities, and plug the same endpoint into any OpenAI client.

---

## What this actually is

notrack.ai's public site talks to `POST /api/dispatch`, which:

- needs **no account and no API key** — a visitor gets an HttpOnly `uid` cookie
- returns **server-sent events**, not JSON
- meters the free tier **per uid**, and also appears to count from the IP
- serves **three speakers**: `C` = NoTrack (its own model), `A` = Minimax, `B` = ChatGPT
- accepts a `instructions` field that becomes the conversation's system prompt,
  stored on the chat row and replayed on every later turn

The panel wraps all of that: it holds a pool of uids, paces requests so the
limiter never trips, retries transparently, and exposes both a browser UI and a
`/v1/chat/completions` endpoint.

## Quick start

```bash
./run.sh
# or
pip install -r requirements.txt
python3 app.py
```

Then open <http://localhost:8080/>.

## The panel

Three tabs in the sidebar:

| Tab | What it does |
|-----|--------------|
| **گفتگو / Chat** | Pick speaker + persona, write a system prompt, chat with streaming and markdown |
| **استخر / Pool** | Live view of every identity: request count, cooldown timer, adaptive send interval. Rotate the whole pool from here |
| **API** | Copy-paste snippet for the OpenAI-compatible endpoint, language toggle (fa/en) |

The pill in the header shows `ready/total · pace` — green means identities are
available, amber means everything is cooling down.

### Speaker routing

| Model name you send | Speaker | Notes |
|---|---|---|
| `notrack-uncensored` | `C` | NoTrack's own model, the default |
| `notrack-minimax` | `A` | Minimax |
| `notrack-chatgpt` | `B` | ChatGPT — was down during recon, probe before relying on it |

Suffix any of them to override persona: `notrack-uncensored:creative`,
`notrack-uncensored:concise`, `:detailed`, `:notrack`, `:bare`.

Use **Probe model liveness** to check which speakers actually answer right now
rather than assuming.

## OpenAI-compatible API

Base URL `http://localhost:8080/v1`, any non-empty API key.

```python
from openai import OpenAI

c = OpenAI(base_url="http://localhost:8080/v1", api_key="panel")

r = c.chat.completions.create(
    model="notrack-uncensored",
    messages=[{"role": "user", "content": "Hello"}],
)
print(r.choices[0].message.content)
```

Streaming works exactly like OpenAI:

```python
for ch in c.chat.completions.create(
    model="notrack-uncensored",
    messages=[{"role": "user", "content": "Count to five"}],
    stream=True,
):
    if ch.choices and ch.choices[0].delta.content:
        print(ch.choices[0].delta.content, end="")
```

### Two ways to keep context

**Session mode** — the panel returns a `session_id` on every response. Pass it
back as `session_id` in the body and the panel reuses the upstream chat:

```python
r1 = c.chat.completions.create(model="notrack-uncensored",
    messages=[{"role":"user","content":"My dog is Biscuit."}])
r2 = c.chat.completions.create(model="notrack-uncensored",
    messages=[{"role":"user","content":"What is my dog's name?"}],
    extra_body={"session_id": r1.session_id})
```

**Stateless mode** — send the whole history in `messages` and omit
`session_id`. The panel folds the transcript into the upstream `instructions`
field, so any framework that resends history (LangChain, LlamaIndex, custom
loops) just works.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/models` | Model list |
| `POST` | `/v1/chat/completions` | Chat, streaming or not |
| `GET` | `/api/health` | Uptime, counters, pool summary |
| `GET` | `/api/pool` | Full identity table |
| `POST` | `/api/pool/rotate` | Discard all uids, mint fresh ones |
| `POST` | `/api/probe` | One cheap request per speaker |
| `POST` | `/api/chat` | Panel's own SSE stream |
| `GET` | `/api/sessions` | Session list |
| `GET` | `/api/sessions/{id}` | One session with turns |
| `DELETE` | `/api/sessions/{id}` | Delete a session |

## How the pool survives the rate limiter

Measured during recon: two rapid requests on one uid pass, the third returns
`{code:"ratelimit"}`. Fresh uids raise the ceiling but do not remove it, because
the limiter also counts from the IP — so rotation alone is not enough.

`core/pool.py` therefore combines:

1. **Rotation** — round-robin across 12 uids, each with its own cooldown timer.
2. **Pacing** — a global interval (starts at 1.9 s) between any two sends.
   It backs off ×1.5 on every 429 and recovers ×0.9 after six clean responses,
   so it settles just under the real limit instead of guessing.
3. **Retry with backoff** — up to 5 attempts, exponential plus jitter, picking a
   different identity each time.
4. **Pinning** — once a chat exists, its uid is locked to that chat. A chat only
   has history on the identity that created it; borrowing a fresh uid silently
   wipes the context. `acquire_uid()` waits out that identity's cooldown rather
   than taking the fast path.

That last point is the one that bites anyone reimplementing this: without
pinning, multi-turn conversations come back as "I don't know your name."

## Limits observed on the free tier

| Limit | Value | Error |
|---|---|---|
| `user_input` | 4000 chars (plus 12000, pro 20000) | `too_long` + `limits` map |
| `instructions` | ~1980 chars | `instr_refused{reason:"too_long"}` |
| Concurrent | 2 on trial, 8 paid | `ratelimit` |
| Requests/min | 60 trial, 300 paid | `ratelimit` |

The panel clamps `instructions` to stay under the cap by keeping the system
prompt and truncating the oldest transcript from the front.

## Layout

```
notrack-panel/
├── app.py               FastAPI app: panel API + OpenAI shim
├── run.sh               launcher
├── requirements.txt
├── core/
│   ├── config.py        endpoints, limits, pacing knobs
│   ├── pool.py          identity pool, cooldowns, adaptive pace
│   ├── upstream.py      SSE client, retries, speaker probing
│   └── store.py         sessions, turns, uid persistence
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js           SSE handling, markdown, i18n (fa/en)
└── data/                pool + session state (created at runtime)
```

## Tuning

Environment variables:

| Var | Default | Meaning |
|---|---|---|
| `PANEL_PACE` | `1.9` | Starting seconds between sends |
| `PANEL_UA` | Chrome 131 desktop | User agent sent upstream |
| `PANEL_DATA` | `./data` | Where state is written |
| `PORT` / `HOST` | `8080` / `0.0.0.0` | Listen address |

If you see repeated `ratelimit` errors, raise `PANEL_PACE` — the adaptive logic
will converge on its own, but starting closer to the real limit is faster.

State lives in `data/identities.json` and `data/sessions.json`. Delete
`identities.json` (or hit **Rotate**) to start with a clean pool.

## Known limits

- Speaker `B` (ChatGPT) returned `The model could not be reached` during recon.
  That is upstream, not the panel — `POST /api/probe` tells you its current state.
- IF the upstream changes its event schema, `_map_event()` in
  `core/upstream.py` is the one place to update.
- Debate mode (`mode:"debate"`, multi-round A↔B↔C with consensus) exists upstream
  but is disabled on the site itself and is not exposed here.
- Token counts in `usage` are estimated from character length; the upstream does
  not report real counts on this endpoint.
"""NoTrack Panel — reverse-engineered gateway + control panel.

Run:  python3 app.py            (default 0.0.0.0:8080)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core import config
from core.pool import IdentityPool
from core.store import SessionStore
from core.upstream import NotrackClient, UpstreamError

app = FastAPI(title="NoTrack Panel", version="1.0.0")

pool = IdentityPool(size=12)
store = SessionStore()
client = NotrackClient(pool)

STATS = {"requests": 0, "errors": 0, "started": time.time()}


# ══════════════════════════════════════════════════════════════ models
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    speaker: str = "C"
    persona: str = "normal"
    system: str = ""


class OpenAIRequest(BaseModel):
    model: str = "notrack-uncensored"
    messages: list[dict] = Field(default_factory=list)
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[Any] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    seed: Optional[int] = None
    n: int = 1
    tools: Optional[list] = None
    tool_choice: Optional[Any] = None
    response_format: Optional[dict] = None
    session_id: Optional[str] = None


# ══════════════════════════════════════════════════════════════ helpers
def _speaker_from_model(model: str) -> tuple[str, str]:
    """Map an incoming model name to (speaker_code, persona_override).

    Accepts: notrack-uncensored, notrack-uncensored:notrack, minimax, chatgpt,
             notrack-uncensored:creative, etc.
    """
    name = (model or "").strip()
    persona = ""
    if ":" in name:
        name, persona = name.split(":", 1)
        name = name.strip()
        persona = persona.strip()

    low = name.lower()
    for prefix in ("notrack-", "no-track-", "nt-"):
        if low.startswith(prefix):
            low = low[len(prefix):]
            break

    if low in ("minimax", "a", "speaker-a", "speaker_a"):
        return "A", persona
    if low in ("chatgpt", "gpt", "b", "speaker-b", "speaker_b", "openai", "gpt-4", "gpt-3.5"):
        return "B", persona
    return "C", persona


def _flatten_messages(messages: list[dict]) -> tuple[str, str]:
    """Split an OpenAI message array into (system_prompt, final_user_text).

    The upstream takes one user_input + one instructions blob. Earlier turns are
    folded into the instructions as a transcript so multi-turn clients keep
    their context even without a stored session.
    """
    system_parts: list[str] = []
    convo: list[dict] = []

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # multimodal content parts
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        content = str(content)
        if role == "system":
            system_parts.append(content)
        else:
            convo.append({"role": role, "content": content})

    last_user = ""
    for m in reversed(convo):
        if m["role"] == "user":
            last_user = m["content"]
            break

    prior = [m for m in convo if m is not convo[-1] or m["content"] != last_user]
    # everything before the final user message becomes context
    idx = len(convo) - 1 - next(
        (i for i, m in enumerate(reversed(convo)) if m["role"] == "user"), 0
    )
    prior = convo[:idx] if idx >= 0 else []

    transcript = ""
    if prior:
        lines = []
        for m in prior:
            who = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{who}: {m['content']}")
        transcript = "\n\n[Conversation so far]\n" + "\n".join(lines)

    system = "\n\n".join(system_parts)
    budget = config.INSTRUCTION_CHAR_CAP
    combined = (system + transcript).strip()

    if len(combined) > budget:
        # keep the system prompt, truncate the older transcript from the front
        keep_sys = system[:budget]
        room = budget - len(keep_sys) - len("\n\n[Conversation so far]\n")
        if room > 200:
            tail = transcript[-room:] if len(transcript) > room else transcript
            combined = keep_sys + "\n\n[Conversation so far]\n" + tail
        else:
            combined = keep_sys

    return combined, last_user


def _openai_response(content: str, model: str, usage: dict) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": usage,
    }


def _approx_usage(prompt: str, completion: str) -> dict:
    pt = max(1, len(prompt) // 4)
    ct = max(1, len(completion) // 4)
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}


# ══════════════════════════════════════════════════════════════ panel api
@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "uptime": round(time.time() - STATS["started"], 1),
        "requests": STATS["requests"],
        "errors": STATS["errors"],
        "pool": {k: v for k, v in pool.stats().items() if k != "identities"},
    }


@app.get("/api/pool")
async def pool_stats():
    return pool.stats()


@app.post("/api/pool/rotate")
async def pool_rotate():
    await pool.rotate_all()
    return {"ok": True, "pool": pool.stats()}


@app.get("/api/models")
async def models():
    data = [{
        "id": "notrack-uncensored",
        "object": "model",
        "created": int(STATS["started"]),
        "owned_by": "notrack",
    }]
    for s in config.SPEAKERS.values():
        if not s["default"]:
            data.append({
                "id": f"notrack-{s['name'].lower()}",
                "object": "model",
                "created": int(STATS["started"]),
                "owned_by": s["vendor"],
            })
    return {"object": "list", "data": data}


@app.post("/api/probe")
async def probe():
    results = []
    for sid in config.SPEAKERS:
        results.append(await client.probe_speaker(sid))
        await asyncio.sleep(1.5)
    return {"results": results, "at": time.time()}


@app.get("/api/sessions")
async def sessions():
    return {"sessions": store.list()}


@app.delete("/api/sessions/{sid}")
async def session_delete(sid: str):
    return {"ok": store.delete(sid)}


@app.get("/api/sessions/{sid}")
async def session_get(sid: str):
    sess = store.get(sid)
    if not sess:
        raise HTTPException(404, "no such session")
    return sess.to_dict()


# ────────────────────────────────────────── panel chat (streaming)
@app.post("/api/chat")
async def panel_chat(req: ChatRequest):
    STATS["requests"] += 1
    speaker, persona_override = _speaker_from_model(req.speaker)
    persona = persona_override or req.persona

    sess = store.get(req.session_id) if req.session_id else None
    if sess is None:
        sess = store.create(speaker=speaker, persona=persona, system=req.system)
    else:
        if req.system:
            sess.system = req.system
        sess.persona = persona

    store.append(sess, "user", req.message)

    async def gen():
        collected: list[str] = []
        try:
            # meta first so the client learns the session id immediately
            yield f"data: {json.dumps({'type':'session','session_id':sess.id,'chat_id':sess.chat_id})}\n\n"
            async for ev in client.dispatch_stream(
                req.message,
                model=speaker,
                persona=persona,
                chat_id=sess.chat_id,
                instructions=sess.system or None,
                pin_uid=sess.uid,
            ):
                if ev.uid:
                    sess.uid = ev.uid
                if ev.type == "chat_meta" and ev.chat_id:
                    sess.chat_id = ev.chat_id
                if ev.type == "delta":
                    collected.append(ev.text)
                    yield f"data: {json.dumps({'type':'delta','text':ev.text,'speaker':ev.speaker})}\n\n"
                elif ev.type == "message":
                    collected = [ev.text]
                    yield f"data: {json.dumps({'type':'message','text':ev.text,'speaker':ev.speaker})}\n\n"
                elif ev.type == "thinking":
                    yield f"data: {json.dumps({'type':'thinking','speaker':ev.speaker})}\n\n"
                elif ev.type in ("error", "instr_refused"):
                    yield f"data: {json.dumps({'type':'error','code':ev.code,'text':ev.text})}\n\n"
            final = "".join(collected)
            store.append(sess, "assistant", final, speaker=speaker)
            yield f"data: {json.dumps({'type':'done','session_id':sess.id,'chat_id':sess.chat_id})}\n\n"
        except UpstreamError as exc:
            STATS["errors"] += 1
            yield f"data: {json.dumps({'type':'error','code':exc.code,'text':exc.message})}\n\n"
        except Exception as exc:
            STATS["errors"] += 1
            yield f"data: {json.dumps({'type':'error','code':'internal','text':str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════════════════════ OpenAI shim
@app.get("/v1/models")
async def v1_models():
    return await models()


@app.post("/v1/chat/completions")
async def v1_chat(req: OpenAIRequest, request: Request):
    STATS["requests"] += 1
    speaker, persona_override = _speaker_from_model(req.model)
    system, user_text = _flatten_messages(req.messages)

    if not user_text:
        raise HTTPException(400, "no user message found")

    persona = persona_override or "normal"

    # Reuse an upstream chat when the caller passes session_id, else start fresh.
    chat_id = None
    sess = store.get(req.session_id) if req.session_id else None
    if sess and sess.chat_id:
        chat_id = sess.chat_id
    if sess is None:
        sess = store.create(speaker=speaker, persona=persona, system=system)
        if req.session_id:
            store._sessions[req.session_id] = sess  # honour caller's id

    instructions = system if not chat_id else None
    model_name = req.model

    if req.stream:
        async def sse():
            collected: list[str] = []
            created = int(time.time())
            rid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            first = {
                "id": rid, "object": "chat.completion.chunk", "created": created,
                "model": model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(first)}\n\n"
            try:
                async for ev in client.dispatch_stream(
                    user_text, model=speaker, persona=persona,
                    chat_id=chat_id, instructions=instructions,
                    pin_uid=sess.uid,
                ):
                    if ev.uid:
                        sess.uid = ev.uid
                    if ev.type == "chat_meta" and ev.chat_id:
                        sess.chat_id = ev.chat_id
                        yield f"data: {json.dumps({'type':'session','chat_id':ev.chat_id,'session_id':sess.id})}\n\n"
                    elif ev.type == "delta" and ev.text:
                        collected.append(ev.text)
                        chunk = {
                            "id": rid, "object": "chat.completion.chunk", "created": created,
                            "model": model_name,
                            "choices": [{"index": 0, "delta": {"content": ev.text}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif ev.type == "message":
                        collected = [ev.text]
                final = "".join(collected)
                store.append(sess, "user", user_text)
                store.append(sess, "assistant", final, speaker=speaker)
                tail = {
                    "id": rid, "object": "chat.completion.chunk", "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": _approx_usage(system + user_text, final),
                }
                yield f"data: {json.dumps(tail)}\n\n"
                yield "data: [DONE]\n\n"
            except UpstreamError as exc:
                err = {"error": {"type": exc.code, "message": exc.message}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                err = {"error": {"type": "internal", "message": str(exc)}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------------------------------------------------- non-stream
    try:
        res = await client.complete(
            user_text, model=speaker, persona=persona,
            chat_id=chat_id, instructions=instructions,
            pin_uid=sess.uid,
        )
        if sess.chat_id is None:
            sess.chat_id = res.get("chat_id")
        if not sess.uid and res.get("uid"):
            sess.uid = res["uid"]
        store.append(sess, "user", user_text)
        store.append(sess, "assistant", res["content"], speaker=speaker)
        out = _openai_response(
            res["content"], model_name,
            _approx_usage(system + user_text, res["content"]),
        )
        out["session_id"] = sess.id
        if sess.chat_id:
            out["notrack_chat_id"] = sess.chat_id
        return JSONResponse(out)
    except UpstreamError as exc:
        STATS["errors"] += 1
        status = 429 if exc.code in ("ratelimit", "exhausted") else 502
        return JSONResponse(
            {"error": {"type": exc.code, "message": exc.message}}, status_code=status
        )


# ══════════════════════════════════════════════════════════════ static
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (config.BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
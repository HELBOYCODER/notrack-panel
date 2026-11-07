"""Upstream client for notrack.ai's anonymous dispatch endpoint.

Protocol notes from recon (2026-11):
  POST https://notrack.ai/api/dispatch
  body: {user_input, mode, model, persona, max_turns, chat_id?, instructions?,
         attachments?, regenerate?, edit?}
  response: text/event-stream, `data: {json}\\n\\n` frames.

Event types observed:
  chat_meta   {chat_id, mode}
  user        {turn, content, message_id}
  thinking    {speaker, turn}
  delta       {speaker, turn, chunk}
  message     {speaker, turn, content, message_id}
  consensus   {speaker, turn, content}      (debate mode)
  instr_refused {reason}
  error       {code, content} | {code, plan, max, limits, content}
  done        {}

Rate limiting: {code:"ratelimit"} frames arrive as SSE, not HTTP 429.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx

from . import config
from .pool import Identity, IdentityPool


@dataclass
class StreamEvent:
    type: str
    speaker: Optional[str] = None
    turn: Optional[int] = None
    text: str = ""
    chat_id: Optional[str] = None
    code: Optional[str] = None
    uid: Optional[str] = None
    raw: dict = field(default_factory=dict)


class UpstreamError(Exception):
    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


class RetryableError(UpstreamError):
    pass


class NotrackClient:
    def __init__(self, pool: IdentityPool) -> None:
        self.pool = pool
        self._client: Optional[httpx.AsyncClient] = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(config.REQUEST_TIMEOUT, connect=20.0),
                headers={
                    "User-Agent": config.USER_AGENT,
                    "Origin": config.NOTRACK_BASE,
                    "Referer": f"{config.NOTRACK_BASE}/chat",
                    "Accept": "text/event-stream, application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ core
    async def dispatch_stream(
        self,
        user_input: str,
        model: str = "C",
        persona: str = "normal",
        chat_id: Optional[str] = None,
        instructions: Optional[str] = None,
        mode: str = "usual",
        max_turns: int = 3,
        regenerate: bool = False,
        pin_uid: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion, transparently retrying rate limits."""
        if instructions and len(instructions) > config.INSTRUCTION_CHAR_CAP:
            raise UpstreamError(
                "instruction_too_long",
                f"instructions exceed {config.INSTRUCTION_CHAR_CAP} characters",
                {"max": config.INSTRUCTION_CHAR_CAP, "got": len(instructions)},
            )
        if len(user_input) > config.USER_INPUT_CAP:
            raise UpstreamError(
                "input_too_long",
                f"user_input exceeds {config.USER_INPUT_CAP} characters",
                {"max": config.USER_INPUT_CAP, "got": len(user_input)},
            )

        payload = {
            "user_input": user_input,
            "mode": mode,
            "model": model,
            "persona": persona,
            "max_turns": max_turns,
        }
        if chat_id:
            payload["chat_id"] = chat_id
        # `instructions` only applies to a brand-new chat; the upstream stores it
        # on the chat row and replays it for later turns.
        if instructions and not chat_id:
            payload["instructions"] = instructions
        if regenerate:
            payload["regenerate"] = True

        last_error: Optional[Exception] = None
        for attempt in range(config.MAX_RETRIES):
            # A chat lives on the uid that created it. When we know that uid we
            # must reuse it or the upstream returns an empty context.
            if pin_uid:
                ident = await self.pool.acquire_uid(pin_uid)
            else:
                ident = await self.pool.acquire()
            await self.pool.pace_gate()

            try:
                got_content = False
                retry_needed = False
                async for ev in self._raw_stream(payload, ident):
                    if ev.type == "ratelimit":
                        retry_needed = True
                        break
                    if ev.type == "error" and ev.code in {"ratelimit", "upstream", "billing", "safety"}:
                        retry_needed = True
                        break
                    got_content = True
                    yield ev
                if not retry_needed:
                    await self.pool.report_ok(ident)
                    return
                await self.pool.report_429(ident)

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (429, 502, 503, 504):
                    await self.pool.report_429(ident)
                    retry_needed = True
                else:
                    await self.pool.report_error(ident)
                    raise UpstreamError(
                        "http_error",
                        f"upstream returned {status}",
                        {"status": status, "body": exc.response.text[:400]},
                    ) from exc
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                await self.pool.report_error(ident)
                retry_needed = True
                last_error = exc

            if retry_needed:
                backoff = min(30.0, 1.6 * (2 ** attempt)) + (attempt * 0.35)
                await asyncio.sleep(backoff)

        raise UpstreamError(
            "exhausted",
            "upstream kept refusing after retries — the IP is likely throttled",
            {"attempts": config.MAX_RETRIES, "last": str(last_error) if last_error else None},
        )

    async def _raw_stream(
        self, payload: dict, ident: Identity
    ) -> AsyncIterator[StreamEvent]:
        url = config.DISPATCH_URL
        async with self._http().stream(
            "POST",
            url,
            json=payload,
            headers={"Content-Type": "application/json", "Cookie": f"uid={ident.uid}"},
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}", request=resp.request, response=resp
                ) if resp.status_code in (429, 502, 503, 504) else UpstreamError(
                    "http_error", f"{resp.status_code}: {body[:300]}", {"status": resp.status_code}
                )

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    for line in block.split("\n"):
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        ev = self._map_event(data)
                        if ev is not None:
                            ev.uid = ident.uid
                            yield ev

    @staticmethod
    def _map_event(data: dict) -> Optional[StreamEvent]:
        t = data.get("type", "")
        if t == "chat_meta":
            return StreamEvent("chat_meta", chat_id=data.get("chat_id"), raw=data)
        if t == "delta":
            return StreamEvent(
                "delta", speaker=data.get("speaker"), turn=data.get("turn"),
                text=data.get("chunk") or "", raw=data,
            )
        if t in ("message", "consensus"):
            return StreamEvent(
                t, speaker=data.get("speaker"), turn=data.get("turn"),
                text=data.get("content") or "", raw=data,
            )
        if t == "thinking":
            return StreamEvent("thinking", speaker=data.get("speaker"),
                               turn=data.get("turn"), raw=data)
        if t == "user":
            return StreamEvent("user", turn=data.get("turn"),
                               text=data.get("content") or "", raw=data)
        if t == "instr_refused":
            return StreamEvent("instr_refused", code=data.get("reason"), raw=data)
        if t == "error":
            return StreamEvent("error", code=data.get("code"),
                               text=data.get("content") or "", raw=data)
        if t == "done":
            return StreamEvent("done", raw=data)
        # unknown type — pass through so nothing is silently swallowed
        return StreamEvent(t or "unknown", raw=data)

    # ------------------------------------------------------------- non-stream
    async def complete(
        self,
        user_input: str,
        model: str = "C",
        persona: str = "normal",
        chat_id: Optional[str] = None,
        instructions: Optional[str] = None,
        pin_uid: Optional[str] = None,
    ) -> dict:
        """Collect a full stream into one response dict."""
        text_parts: list[str] = []
        chat_id_out = chat_id
        speaker = model
        uid_out = pin_uid
        async for ev in self.dispatch_stream(
            user_input, model=model, persona=persona,
            chat_id=chat_id, instructions=instructions, pin_uid=pin_uid,
        ):
            if ev.uid:
                uid_out = ev.uid
            if ev.type == "chat_meta" and ev.chat_id:
                chat_id_out = ev.chat_id
            elif ev.type == "delta":
                text_parts.append(ev.text)
                speaker = ev.speaker or speaker
            elif ev.type == "message":
                text_parts = [ev.text]
                speaker = ev.speaker or speaker
            elif ev.type == "instr_refused":
                raise UpstreamError("instruction_refused", "upstream refused the system prompt")
            elif ev.type == "error":
                raise UpstreamError(ev.code or "error", ev.text or "upstream error")
        return {
            "content": "".join(text_parts),
            "chat_id": chat_id_out,
            "uid": uid_out,
            "speaker": speaker,
        }

    # --------------------------------------------------------------- history
    async def history(self, chat_id: str, uid: Optional[str] = None) -> dict:
        headers = {"User-Agent": config.USER_AGENT, "Referer": f"{config.NOTRACK_BASE}/chat"}
        if uid:
            headers["Cookie"] = f"uid={uid}"
        r = await self._http().get(config.CHAT_URL, params={"id": chat_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    async def chat_list(self, uid: str) -> dict:
        r = await self._http().get(
            config.CHATS_URL,
            headers={"User-Agent": config.USER_AGENT, "Cookie": f"uid={uid}",
                     "Referer": f"{config.NOTRACK_BASE}/chat"},
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ probe
    async def probe_speaker(self, speaker: str) -> dict:
        """Ask a speaker one cheap question and report whether it answered."""
        try:
            res = await self.complete(
                "Reply with exactly: ALIVE", model=speaker, persona="concise"
            )
            alive = bool(res["content"].strip())
            return {"speaker": speaker, "alive": alive,
                    "sample": res["content"].strip()[:120]}
        except Exception as exc:
            return {"speaker": speaker, "alive": False, "error": str(exc)}
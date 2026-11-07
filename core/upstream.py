"""Single-identity upstream client; refusals and ambiguous failures never retry.

ponytail: intentionally one process, one worker, one operator. The process-wide
lock covers bootstrap, pacing, and the entire dispatch stream, not just headers.
"""
from __future__ import annotations

import asyncio
import json
import math
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Optional

import httpx

from . import config
from .pool import Identity, IdentityError, IdentityPool

_DISPATCH_LOCK = asyncio.Lock()


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
    """Legacy import compatibility only; the client never retries requests."""


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
                verify=True,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _retry_after(value: Optional[str]) -> int:
        delay = config.RATE_LIMIT_COOLDOWN
        if value:
            try:
                delay = float(value)
                if not math.isfinite(delay):
                    raise ValueError
            except ValueError:
                try:
                    when = parsedate_to_datetime(value)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    delay = (when - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = config.RATE_LIMIT_COOLDOWN
        return math.ceil(max(config.RATE_LIMIT_COOLDOWN, min(config.MAX_RETRY_AFTER, delay)))

    @classmethod
    def _check_status(cls, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 429:
            raise UpstreamError("ratelimit", "Upstream rate limit reached", {
                "status": status, "retry_after": cls._retry_after(response.headers.get("retry-after"))})
        # Never include response bodies, headers, URLs, cookies, or exception text.
        raise UpstreamError("http_error", "Upstream request was rejected", {"status": status})

    async def _ensure_uid(self, pin_uid: Optional[str] = None) -> str:
        if self.pool.uid is None:
            client = self._http()
            response = await client.get(f"{config.NOTRACK_BASE}/chat")
            self._check_status(response)
            try:
                uid = response.cookies.get("uid")
            except httpx.CookieConflict:
                raise UpstreamError("invalid_identity", "Ambiguous upstream identity cookie") from None
            if not uid:
                raise UpstreamError("identity_missing", "Upstream did not issue an identity cookie")
            await self.pool.bind_uid(uid)
        if pin_uid is not None and pin_uid != self.pool.uid:
            raise UpstreamError("identity_mismatch", "Session does not match the stable upstream identity")
        return self.pool.uid

    @staticmethod
    def _identity_error(exc: IdentityError) -> UpstreamError:
        detail = {"retry_after": exc.retry_after} if exc.retry_after else None
        return UpstreamError(exc.code, exc.message, detail)

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
        """Send exactly one POST. Cancellation releases response and global lock."""
        if instructions and len(instructions) > config.INSTRUCTION_CHAR_CAP:
            raise UpstreamError("instruction_too_long", f"instructions exceed {config.INSTRUCTION_CHAR_CAP} characters",
                                {"max": config.INSTRUCTION_CHAR_CAP, "got": len(instructions)})
        if len(user_input) > config.USER_INPUT_CAP:
            raise UpstreamError("input_too_long", f"user_input exceeds {config.USER_INPUT_CAP} characters",
                                {"max": config.USER_INPUT_CAP, "got": len(user_input)})
        payload = {"user_input": user_input, "mode": mode, "model": model,
                   "persona": persona, "max_turns": max_turns}
        if chat_id:
            payload["chat_id"] = chat_id
        if instructions and not chat_id:
            payload["instructions"] = instructions
        if regenerate:
            payload["regenerate"] = True

        async with _DISPATCH_LOCK:
            ident = None
            try:
                await self._ensure_uid(pin_uid)
                ident = await (self.pool.acquire_uid(pin_uid) if pin_uid is not None else self.pool.acquire())
                await self.pool.pace_gate()
                content = ""
                final_message = False
                done = False
                async with aclosing(self._raw_stream(payload, ident)) as stream:
                    async for ev in stream:
                        if ev.type == "delta":
                            content += ev.text
                        elif ev.type in ("message", "consensus"):
                            content = ev.text
                            final_message = True
                        elif ev.type == "done":
                            done = True
                            if not content:
                                raise UpstreamError("empty_response", "Upstream returned no assistant content")
                        yield ev
                        if done:
                            break
                if not content or not (done or final_message):
                    raise UpstreamError("incomplete_response", "Upstream response was empty or incomplete")
                await self.pool.report_ok(ident)
            except UpstreamError as exc:
                if ident is not None:
                    try:
                        if exc.code == "ratelimit":
                            delay = self._retry_after(str(exc.detail.get("retry_after", 60)))
                            await self.pool.report_429(ident, delay)
                            exc.detail["retry_after"] = delay
                        else:
                            await self.pool.report_error(ident)
                    except IdentityError as state_error:
                        raise self._identity_error(state_error) from None
                raise
            except IdentityError as exc:
                raise self._identity_error(exc) from None
            except httpx.HTTPError:
                if ident is not None:
                    try:
                        await self.pool.report_error(ident)
                    except IdentityError as exc:
                        raise self._identity_error(exc) from None
                raise UpstreamError("transport_error", "Upstream connection failed; request was not retried") from None

    async def _raw_stream(self, payload: dict, ident: Identity) -> AsyncIterator[StreamEvent]:
        async with self._http().stream(
            "POST", config.DISPATCH_URL, json=payload,
            headers={"Content-Type": "application/json", "Cookie": f"uid={ident.uid}"},
        ) as response:
            self._check_status(response)
            data_lines = []
            event_type = None
            # httpx handles UTF-8 and CRLF even when bytes split across chunks.
            async for line in response.aiter_lines():
                if not line:
                    if data_lines:
                        yield self._decode_event(data_lines, event_type, ident.uid)
                    data_lines = []
                    event_type = None
                elif line.startswith("data:"):
                    value = line[5:]
                    data_lines.append(value[1:] if value.startswith(" ") else value)
                elif line.startswith("event:"):
                    event_type = line[6:].strip()
            # Some servers omit the final blank line at EOF.
            if data_lines:
                yield self._decode_event(data_lines, event_type, ident.uid)

    @classmethod
    def _decode_event(cls, lines: list[str], event_type: Optional[str], uid: str) -> StreamEvent:
        if sum(map(len, lines)) > 1048576:
            raise UpstreamError('protocol_error', 'Upstream event exceeds size limit')
        try:
            data = json.loads("\n".join(lines))
        except (ValueError, TypeError):
            raise UpstreamError("protocol_error", "Invalid upstream event") from None
        if not isinstance(data, dict):
            raise UpstreamError("protocol_error", "Invalid upstream event")
        if event_type and not data.get("type"):
            data["type"] = event_type
        ev = cls._map_event(data)
        ev.uid = uid
        return ev

    @staticmethod
    def _map_event(data: dict) -> StreamEvent:
        t = data.get("type", "")
        if not isinstance(t, str):
            raise UpstreamError("protocol_error", "Invalid upstream event type")
        for key in ("speaker", "chat_id"):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise UpstreamError("protocol_error", "Invalid upstream event metadata")
        if data.get("turn") is not None and not isinstance(data["turn"], int):
            raise UpstreamError("protocol_error", "Invalid upstream event metadata")
        if t == "instr_refused":
            raise UpstreamError("instruction_refused", "Upstream refused the instructions")
        if t in ("error", "ratelimit"):
            code = "ratelimit" if t == "ratelimit" else data.get("code")
            allowed = {"ratelimit", "upstream", "billing", "safety", "quota", "unauthorized", "forbidden"}
            if not isinstance(code, str) or code not in allowed:
                code = "upstream_error"
            detail = {"retry_after": 60} if code == "ratelimit" else None
            raise UpstreamError(code, "Upstream rate limit reached" if code == "ratelimit" else "Upstream refused the request", detail)
        text = data.get("chunk") if t == "delta" else data.get("content")
        if text is not None and not isinstance(text, str):
            raise UpstreamError("protocol_error", "Invalid upstream event content")
        if t == "chat_meta":
            return StreamEvent("chat_meta", chat_id=data.get("chat_id"), raw=data)
        if t in ("delta", "message", "consensus", "user", "thinking"):
            return StreamEvent(t, speaker=data.get("speaker"), turn=data.get("turn"), text=text or "", raw=data)
        if t == "done":
            return StreamEvent("done", raw=data)
        return StreamEvent("unknown", raw=data)

    async def complete(
        self,
        user_input: str,
        model: str = "C",
        persona: str = "normal",
        chat_id: Optional[str] = None,
        instructions: Optional[str] = None,
        pin_uid: Optional[str] = None,
    ) -> dict:
        text_parts = []
        chat_id_out = chat_id
        speaker = model
        uid_out = None
        async with aclosing(self.dispatch_stream(
            user_input, model=model, persona=persona, chat_id=chat_id,
            instructions=instructions, pin_uid=pin_uid,
        )) as stream:
            async for ev in stream:
                uid_out = ev.uid or uid_out
                if ev.type == "chat_meta" and ev.chat_id:
                    chat_id_out = ev.chat_id
                elif ev.type == "delta":
                    text_parts.append(ev.text)
                    speaker = ev.speaker or speaker
                elif ev.type in ("message", "consensus"):
                    text_parts = [ev.text]
                    speaker = ev.speaker or speaker
        return {"content": "".join(text_parts), "chat_id": chat_id_out,
                "uid": uid_out, "speaker": speaker}

    async def _get_json(self, url: str, uid: Optional[str], params: Optional[dict] = None) -> dict:
        async with _DISPATCH_LOCK:
            ident = None
            try:
                stable_uid = await self._ensure_uid(uid)
                ident = await self.pool.acquire_uid(stable_uid)
                await self.pool.pace_gate()
                response = await self._http().get(url, params=params, headers={"Cookie": f"uid={stable_uid}"})
                self._check_status(response)
                try:
                    result = response.json()
                except ValueError:
                    raise UpstreamError("protocol_error", "Invalid upstream response") from None
                if not isinstance(result, (dict, list)):
                    raise UpstreamError("protocol_error", "Invalid upstream response")
                if isinstance(result, dict) and result.get("type") in ("error", "instr_refused", "ratelimit"):
                    self._map_event(result)
                await self.pool.report_ok(ident)
                return result
            except UpstreamError as exc:
                if ident is not None:
                    try:
                        if exc.code == "ratelimit":
                            await self.pool.report_429(ident, exc.detail.get("retry_after", 60))
                        else:
                            await self.pool.report_error(ident)
                    except IdentityError as state_error:
                        raise self._identity_error(state_error) from None
                raise
            except IdentityError as exc:
                raise self._identity_error(exc) from None
            except httpx.HTTPError:
                raise UpstreamError("transport_error", "Upstream connection failed; request was not retried") from None

    async def history(self, chat_id: str, uid: Optional[str] = None) -> dict:
        return await self._get_json(config.CHAT_URL, uid, {"id": chat_id})

    async def chat_list(self, uid: str) -> dict:
        return await self._get_json(config.CHATS_URL, uid)

    async def probe_speaker(self, speaker: str) -> dict:
        """Explicit caller-initiated check; never performs background retries."""
        try:
            result = await self.complete("Reply with exactly: ALIVE", model=speaker, persona="concise")
            return {"speaker": speaker, "alive": bool(result["content"].strip()),
                    "sample": result["content"].strip()[:120]}
        except UpstreamError as exc:
            return {"speaker": speaker, "alive": False, "error": exc.message}

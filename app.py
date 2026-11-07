"""Single-operator NoTrack web adapter. Run through ./run.sh."""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from core.body_limit import RequestBodyLimitMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from core import config
from core.pool import IdentityPool
from core.store import SessionStore
from core.upstream import NotrackClient, UpstreamError

pool = IdentityPool(size=1)
store = SessionStore()
client = NotrackClient(pool)
log = logging.getLogger('notrack-panel')
STATS = {'requests': 0, 'errors': 0, 'started': time.time()}
# ponytail: one process and one trusted operator — upgrade to a DB and per-user ACLs before shared hosting.
BUSY: set[str] = set()


@asynccontextmanager
async def lifespan(app):
    yield
    await client.close()


app = FastAPI(title='NoTrack Panel', version='0.1.2', docs_url=None,
              redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=config.MAX_REQUEST_BYTES)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Do not echo rejected tools, prompts, credentials, or their raw values.
    return JSONResponse({'error': {'type': 'unsupported_request',
        'message': 'This adapter is text-only. Native tools, tool messages, images, and unsupported parameters are not accepted.'},
        'detail': [{'type': error['type'], 'loc': error['loc']} for error in exc.errors()]}, status_code=422)


@app.middleware('http')
async def private_panel(request: Request, call_next):
    token = config.PANEL_TOKEN
    if not token:
        return JSONResponse({'error': {'type': 'configuration', 'message': 'Set PANEL_TOKEN before starting the panel.'}}, status_code=503)
    authorization = request.headers.get('authorization', '')
    supplied = ''
    if authorization.startswith('Bearer '):
        supplied = authorization[7:]
    elif authorization.startswith('Basic '):
        try:
            username, supplied = base64.b64decode(authorization[6:], validate=True).decode().split(':', 1)
            if username != 'panel':
                supplied = ''
        except (ValueError, UnicodeError, binascii.Error):
            supplied = ''
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        return JSONResponse({'error': {'type': 'authentication', 'message': 'Authentication required.'}},
                            status_code=401, headers={'WWW-Authenticate': 'Basic realm="NoTrack Panel", charset="UTF-8"'})
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': {'type': 'origin', 'message': 'Cross-origin writes are not allowed.'}}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = Field(default=None, max_length=64)
    speaker: str = 'C'
    persona: Literal['normal', 'concise', 'detailed', 'creative'] = 'normal'
    system: str = Field(default='', max_length=1980)


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['system', 'user', 'assistant']
    content: str = Field(max_length=16000)


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    include_usage: bool = Field(default=False, strict=True)


class OpenAIRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = 'notrack-uncensored'
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: bool = False
    session_id: Optional[str] = Field(default=None, max_length=64)
    # ponytail: web dispatch has no native token cap or token accounting — upgrade to the official API when exact budgets/usage are required.
    max_completion_tokens: Optional[int] = Field(default=None, ge=1, le=1000000, strict=True)
    stream_options: Optional[StreamOptions] = None

    def compatibility_warnings(self) -> list[str]:
        warnings = []
        if self.max_completion_tokens is not None:
            warnings.append('max_completion_tokens is advisory only: the web backend cannot enforce a token limit.')
        if self.stream_options and self.stream_options.include_usage:
            warnings.append('Token usage is unavailable from the web backend; no usage totals will be fabricated.')
        return warnings


def _speaker_from_model(model: str) -> tuple[str, str]:
    name, sep, persona = model.lower().partition(':')
    aliases = {'c': 'C', 'a': 'A', 'b': 'B', 'notrack-uncensored': 'C',
               'minimax': 'A', 'notrack-minimax': 'A', 'chatgpt': 'B', 'notrack-chatgpt': 'B'}
    if name not in aliases:
        raise HTTPException(400, 'Unknown model name')
    if sep and persona not in config.PERSONAS:
        raise HTTPException(400, 'Unsupported persona; web adapter supports normal, concise, detailed, creative only')
    return aliases[name], persona


def _flatten_messages(messages: list[Message]) -> tuple[str, str]:
    if messages[-1].role != 'user' or not messages[-1].content.strip():
        raise HTTPException(400, 'The last message must be a nonempty user message')
    systems = [m.content for m in messages[:-1] if m.role == 'system']
    prior = [f'{m.role.title()}: {m.content}' for m in messages[:-1] if m.role != 'system']
    context = '\n\n'.join(systems)
    if prior:
        context += '\n\n[Conversation so far]\n' + '\n'.join(prior)
    context = context.strip()
    if len(context) > config.INSTRUCTION_CHAR_CAP:
        raise HTTPException(400, 'History/system prompt exceeds web instructions cap; use session_id or the official API. Nothing was truncated.')
    if len(messages[-1].content) > config.USER_INPUT_CAP:
        raise HTTPException(400, 'User input exceeds web limit')
    return context, messages[-1].content


def _session(sid, speaker, persona, system):
    sess = store.get(sid) if sid else None
    if sid and sess is None:
        raise HTTPException(404, 'No such session')
    if sess:
        if sess.speaker != speaker or (system and system != sess.system):
            raise HTTPException(409, 'Create a new chat to change model or system prompt')
    else:
        sess = store.create(speaker=speaker, persona=persona, system=system)
    if sess.id in BUSY:
        raise HTTPException(409, 'This session already has a request in progress')
    return sess


def _event(data):
    return 'data: ' + json.dumps(data, ensure_ascii=False) + '\n\n'


def _error(ev):
    if ev.type in {'error', 'instr_refused'}:
        raise UpstreamError(ev.code or ev.type, ev.text or 'Upstream refused the request')


def _status(exc):
    if exc.code in {'ratelimit', 'rate_limit', 'cooldown'}:
        return 429
    if exc.code in {'input_too_long', 'instruction_too_long', 'instruction_refused'}:
        return 400
    return 502


@app.get('/api/health')
async def health():
    return {'ok': True, 'uptime': round(time.time() - STATS['started'], 1),
            'requests': STATS['requests'], 'errors': STATS['errors'],
            'pool': {k: v for k, v in pool.stats().items() if k != 'identities'}}


@app.get('/api/pool')
async def pool_stats():
    result = pool.stats()
    result['identities'] = [{k: v for k, v in item.items() if k != 'uid'} for item in result['identities']]
    return result


@app.get('/api/models')
@app.get('/v1/models')
async def models():
    return {'object': 'list', 'data': [
        {'id': 'notrack-uncensored', 'object': 'model', 'owned_by': 'unverified-web-label'},
        {'id': 'notrack-minimax', 'object': 'model', 'owned_by': 'unverified-web-label', 'experimental': True},
        {'id': 'notrack-chatgpt', 'object': 'model', 'owned_by': 'unverified-web-label', 'experimental': True}]}


@app.post('/api/probe')
async def probe():
    return {'results': [await client.probe_speaker(s) for s in config.SPEAKERS], 'at': time.time()}


@app.get('/api/sessions')
async def sessions():
    return {'sessions': store.list()}


@app.get('/api/sessions/{sid}')
async def session_get(sid: str):
    sess = store.get(sid)
    if not sess:
        raise HTTPException(404, 'No such session')
    result = sess.to_dict()
    result.pop('uid', None)
    return result


@app.delete('/api/sessions/{sid}')
async def session_delete(sid: str):
    if sid in BUSY:
        raise HTTPException(409, 'Session in progress')
    return {'ok': store.delete(sid)}


async def _turn(sess, text, speaker, persona):
    """Normalize a turn; never pretend partial/error output is complete."""
    content = ''
    BUSY.add(sess.id)
    try:
        async for ev in client.dispatch_stream(text, model=speaker, persona=persona,
                chat_id=sess.chat_id, instructions=sess.system or None, pin_uid=sess.uid):
            _error(ev)
            if ev.uid:
                sess.uid = ev.uid
            if ev.type == 'chat_meta' and ev.chat_id:
                sess.chat_id = ev.chat_id
                store.save()
            if ev.type == 'delta':
                content += ev.text
                yield ev
            elif ev.type == 'message':
                if content and not ev.text.startswith(content):
                    raise UpstreamError('stream_mismatch', 'Final answer differs from streamed text')
                suffix = ev.text[len(content):]
                content = ev.text
                if suffix:
                    from core.upstream import StreamEvent
                    yield StreamEvent('delta', text=suffix, speaker=ev.speaker)
            elif ev.type == 'thinking':
                yield ev
        if not content:
            raise UpstreamError('empty', 'Upstream returned no answer')
        store.append(sess, 'user', text)
        store.append(sess, 'assistant', content, speaker=speaker)
    finally:
        BUSY.discard(sess.id)


@app.post('/api/chat')
async def panel_chat(req: ChatRequest):
    speaker, override = _speaker_from_model(req.speaker)
    persona = override or req.persona
    sess = _session(req.session_id, speaker, persona, req.system)
    BUSY.add(sess.id)
    STATS['requests'] += 1

    async def gen():
        try:
            yield _event({'type': 'session', 'session_id': sess.id})
            async for ev in _turn(sess, req.message, speaker, persona):
                yield _event({'type': ev.type, 'text': ev.text, 'speaker': ev.speaker})
            yield _event({'type': 'done', 'session_id': sess.id})
        except UpstreamError as exc:
            STATS['errors'] += 1
            yield _event({'type': 'error', 'code': exc.code, 'text': exc.message})
        except Exception:
            STATS['errors'] += 1
            log.error('Panel turn failed (details withheld)')
            yield _event({'type': 'error', 'code': 'internal', 'text': 'Request failed'})
        finally:
            BUSY.discard(sess.id)
    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'X-Accel-Buffering': 'no'})


@app.post('/v1/chat/completions')
async def v1_chat(req: OpenAIRequest):
    speaker, override = _speaker_from_model(req.model)
    persona = override or 'normal'
    if req.session_id:
        # Existing sessions carry their own history. Accept only a final user turn
        # plus an optional unchanged system prompt; never silently discard history.
        if any(m.role == 'assistant' for m in req.messages) or sum(m.role == 'user' for m in req.messages) != 1:
            raise HTTPException(400, 'With session_id send only the new user message and optional unchanged system prompt')
    system, text = _flatten_messages(req.messages)
    sess = _session(req.session_id, speaker, persona, system)
    BUSY.add(sess.id)
    STATS['requests'] += 1
    rid = 'chatcmpl-' + uuid.uuid4().hex
    created = int(time.time())
    warnings = req.compatibility_warnings()
    response_headers = {'X-Session-Id': sess.id, 'X-Accel-Buffering': 'no',
                        'X-NoTrack-Usage': 'unavailable'}
    if req.max_completion_tokens is not None:
        response_headers['X-NoTrack-Token-Limit'] = 'advisory-only'

    def chunk(delta, reason=None):
        result = {'id': rid, 'object': 'chat.completion.chunk', 'created': created,
                  'model': req.model, 'session_id': sess.id,
                  'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
        if delta.get('role') == 'assistant' and warnings:
            result['notrack'] = {'warnings': warnings}
        return result

    if req.stream:
        async def sse():
            try:
                yield _event(chunk({'role': 'assistant'}))
                async for ev in _turn(sess, text, speaker, persona):
                    if ev.type == 'delta':
                        yield _event(chunk({'content': ev.text}))
                yield _event(chunk({}, 'stop'))
            except UpstreamError as exc:
                STATS['errors'] += 1
                yield _event({'error': {'type': exc.code, 'message': exc.message}})
            except Exception:
                STATS['errors'] += 1
                log.error('API stream failed (details withheld)')
                yield _event({'error': {'type': 'internal', 'message': 'Request failed'}})
            finally:
                BUSY.discard(sess.id)
            yield 'data: [DONE]\n\n'
        return StreamingResponse(sse(), media_type='text/event-stream', headers=response_headers)

    try:
        parts = []
        async for ev in _turn(sess, text, speaker, persona):
            if ev.type == 'delta':
                parts.append(ev.text)
        result = {'id': rid, 'object': 'chat.completion', 'created': created,
                  'model': req.model, 'session_id': sess.id,
                  'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': ''.join(parts)}, 'finish_reason': 'stop'}]}
        if warnings:
            result['notrack'] = {'warnings': warnings}
        return JSONResponse(result, headers=response_headers)
    except UpstreamError as exc:
        STATS['errors'] += 1
        return JSONResponse({'error': {'type': exc.code, 'message': exc.message}}, status_code=_status(exc),
                headers={'Retry-After': str(int(exc.detail.get('retry_after', 60)))} if _status(exc) == 429 else None)
    except Exception:
        STATS['errors'] += 1
        log.error('API request failed (details withheld)')
        return JSONResponse({'error': {'type': 'internal', 'message': 'Request failed'}}, status_code=500)
    finally:
        BUSY.discard(sess.id)


app.mount('/static', StaticFiles(directory=str(config.BASE_DIR / 'static')), name='static')


@app.get('/', response_class=HTMLResponse)
async def index():
    return (config.BASE_DIR / 'static' / 'index.html').read_text(encoding='utf-8')


if __name__ == '__main__':
    import os
    import uvicorn
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'),
                port=int(os.environ.get('PORT', '8080')), log_level='info')

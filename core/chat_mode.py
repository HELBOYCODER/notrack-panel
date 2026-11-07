"""Explicit 'chat-only' conversion for agent clients (e.g. Minis) using model `notrack-chat`.

The web backend cannot run tools or accept a long system prompt. Instead of rejecting
every real chat turn, this opt-in model deliberately keeps only the conversation text:
  - drops system/developer prompts and tool definitions (the client's agent scaffolding)
  - drops tool-call / tool-result messages
  - keeps user/assistant text, newest first, within the web budget
Every dropped item is reported in response headers/warnings; nothing is silent.
ponytail: text-only chat mode — upgrade to a tool-capable backend for real Agent use.
"""
from __future__ import annotations

CHAT_MODEL = 'notrack-chat'
_TRANSPORT_KEYS = {'stream', 'max_completion_tokens', 'stream_options', 'session_id'}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(p.get('text', '') for p in content
                         if isinstance(p, dict) and p.get('type') in ('text', 'input_text'))
    return ''


def convert(body: dict, context_cap: int, input_cap: int) -> tuple[dict, list[str]]:
    """Return (strict text-only request body, warnings). Raises ValueError if unusable."""
    notes: list[str] = []
    ignored = sorted(k for k in body if k not in _TRANSPORT_KEYS | {'model', 'messages'})
    if ignored:
        notes.append('ignored parameters: ' + ', '.join(ignored))
    convo, dropped_system, dropped_tool, dropped_media = [], 0, 0, 0
    for m in body.get('messages') or []:
        role = m.get('role') if isinstance(m, dict) else None
        if role in ('system', 'developer'):
            dropped_system += 1
            continue
        if role == 'tool' or (role == 'assistant' and m.get('tool_calls') and not _text(m.get('content'))):
            dropped_tool += 1
            continue
        if role not in ('user', 'assistant'):
            continue
        text = _text(m.get('content')).strip()
        if isinstance(m.get('content'), list) and any(
                isinstance(p, dict) and p.get('type') not in ('text', 'input_text') for p in m['content']):
            dropped_media += 1
        if text:
            convo.append({'role': role, 'content': text})
    if dropped_system:
        notes.append(f'dropped {dropped_system} system prompt(s) (agent scaffolding)')
    if dropped_tool:
        notes.append(f'dropped {dropped_tool} tool message(s); tools are unavailable')
    if dropped_media:
        notes.append('non-text parts dropped; images/files are unavailable')
    while convo and convo[-1]['role'] != 'user':
        convo.pop()
    if not convo:
        raise ValueError('No user text message to send')
    last = convo[-1]
    if len(last['content']) > input_cap:
        raise ValueError(f'Last message is {len(last["content"])} chars; web limit is {input_cap}')
    # keep newest prior turns that fit the instruction budget (with transcript overhead)
    kept, used, overhead = [], 0, len('\n\n[Conversation so far]\n')
    for m in reversed(convo[:-1]):
        line = len(m['role'].title()) + 2 + len(m['content']) + 1
        if used + line + overhead > context_cap:
            break
        kept.append(m)
        used += line
    kept.reverse()
    trimmed = len(convo) - 1 - len(kept)
    if trimmed:
        notes.append(f'{trimmed} older turn(s) not sent (web context budget {context_cap} chars)')
    out = {k: body[k] for k in _TRANSPORT_KEYS if k in body and k != 'session_id'}
    out['model'] = 'notrack-uncensored'
    out['messages'] = kept + [last]
    return out, notes

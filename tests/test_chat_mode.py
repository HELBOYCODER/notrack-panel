"""notrack-chat: real agent-client payloads become explicit text-only chat."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ.setdefault('PANEL_DATA', TEMP.name)
from fastapi.testclient import TestClient
import app as panel
from core.upstream import StreamEvent

AGENT_BODY = {
    'model': 'notrack-chat', 'stream': True, 'max_completion_tokens': 4096,
    'stream_options': {'include_usage': True}, 'temperature': 0.7,
    'tools': [{'type': 'function', 'function': {'name': 'shell', 'parameters': {}}}],
    'messages': [
        {'role': 'system', 'content': 'AGENT_SCAFFOLD ' * 3000},
        {'role': 'user', 'content': 'My name is Kian.'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': '1', 'type': 'function'}]},
        {'role': 'tool', 'tool_call_id': '1', 'content': 'tool output'},
        {'role': 'assistant', 'content': 'Hi Kian.'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'What is my name?'}]},
    ]}


class ChatModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = patch.object(panel.config, 'PANEL_TOKEN', 'chat-mode-token')
        self.cfg.start()
        self.http = TestClient(panel.app)
        self.h = {'Authorization': 'Bearer chat-mode-token'}
        self.calls = []
        async def answer(text, **kw):
            self.calls.append((text, kw))
            yield StreamEvent('message', text='Your name is Kian.', speaker='C')
            yield StreamEvent('done')
        self.up = patch.object(panel.client, 'dispatch_stream', answer)
        self.up.start()

    def tearDown(self):
        self.up.stop(); self.cfg.stop()
        self.assertFalse(panel.BUSY)

    def test_real_agent_payload_answers_with_disclosure(self):
        r = self.http.post('/v1/chat/completions', headers=self.h, json=AGENT_BODY)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('Your name is Kian.', r.text)
        self.assertTrue(r.text.endswith('data: [DONE]\n\n'))
        mode = r.headers['x-notrack-chat-mode']
        self.assertIn('system prompt', mode)
        self.assertIn('tool', mode)
        self.assertIn('temperature', mode)
        text, kw = self.calls[0]
        self.assertEqual(text, 'What is my name?')
        self.assertNotIn('AGENT_SCAFFOLD', kw['instructions'] or '')
        self.assertIn('Kian', kw['instructions'])
        self.assertNotIn('tool output', kw['instructions'])

    def test_long_history_is_trimmed_not_rejected(self):
        body = dict(AGENT_BODY, stream=False, messages=[
            {'role': 'user', 'content': 'old ' * 600}, {'role': 'assistant', 'content': 'ok ' * 600},
            {'role': 'user', 'content': 'latest question'}])
        r = self.http.post('/v1/chat/completions', headers=self.h, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('older turn', r.headers['x-notrack-chat-mode'])
        self.assertLessEqual(len(self.calls[0][1]['instructions'] or ''), panel.config.INSTRUCTION_CHAR_CAP)

    def test_strict_model_still_rejects_tools(self):
        r = self.http.post('/v1/chat/completions', headers=self.h, json=dict(AGENT_BODY, model='notrack-uncensored'))
        self.assertEqual(r.status_code, 422)
        self.assertIn('notrack-chat', r.text)
        self.assertFalse(self.calls)

    def test_no_user_text_is_clear_error(self):
        body = dict(AGENT_BODY, messages=[{'role': 'system', 'content': 'x'}])
        r = self.http.post('/v1/chat/completions', headers=self.h, json=body)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.calls)

    def test_model_listed(self):
        ids = [m['id'] for m in self.http.get('/v1/models', headers=self.h).json()['data']]
        self.assertIn('notrack-chat', ids)

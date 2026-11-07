"""Bounded HTTP body handling, including bodies without Content-Length."""
import asyncio
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


class RequestSizeTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.object(panel.config, 'PANEL_TOKEN', 'test-body-limit-token')
        self.config.start()
        self.http = TestClient(panel.app)
        self.headers = {'Authorization': 'Bearer test-body-limit-token', 'Content-Type': 'application/json'}
        self.calls = []
        async def answer(*args, **kwargs):
            self.calls.append((args, kwargs))
            yield StreamEvent('message', text='OK', speaker='C')
            yield StreamEvent('done')
        self.upstream = patch.object(panel.client, 'dispatch_stream', answer)
        self.upstream.start()
        self.body = json.dumps({'messages': [{'role': 'user', 'content': 'Reply OK'}],
                               'max_completion_tokens': 128, 'stream_options': {'include_usage': True}}).encode()

    def tearDown(self):
        self.upstream.stop()
        self.config.stop()
        self.assertFalse(panel.BUSY)

    def test_valid_body_above_old_64k_limit_is_processed(self):
        # JSON whitespace is valid and exercises transport size, not model context.
        body = self.body + b' ' * 70000
        r = self.http.post('/v1/chat/completions', headers=self.headers, content=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['choices'][0]['message']['content'], 'OK')
        self.assertEqual(len(self.calls), 1)

    def test_oversized_body_error_is_structured_and_has_budget(self):
        body = self.body + b' ' * 1048576
        r = self.http.post('/v1/chat/completions', headers=self.headers, content=body)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()['error']['type'], 'request_too_large')
        self.assertEqual(r.json()['error']['limit_bytes'], 1048576)
        self.assertFalse(self.calls)

    def test_invalid_content_length_is_rejected(self):
        for length in ('-1', 'invalid'):
            with self.subTest(length=length):
                r = self.http.post('/v1/chat/completions', content=self.body,
                    headers={**self.headers, 'Content-Length': length})
                self.assertEqual(r.status_code, 400)
        self.assertFalse(self.calls)

    def test_large_agent_schema_is_not_silently_dropped(self):
        body = {'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [{'type': 'function', 'function': {'name': 'example', 'description': 'TOOL_SECRET' * 8000}}]}
        r = self.http.post('/v1/chat/completions', headers=self.headers, json=body)
        self.assertEqual(r.status_code, 422)
        self.assertIn('text-only', r.text)
        self.assertNotIn('TOOL_SECRET', r.text)
        self.assertFalse(self.calls)

    def test_auth_is_checked_before_request_size(self):
        r = self.http.post('/v1/chat/completions', content=b'x' * 1048577)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self.calls)


class ReceiveLimitTests(unittest.IsolatedAsyncioTestCase):
    async def setup_limit(self, chunks, limit=8, headers=None):
        from core.body_limit import RequestBodyLimitMiddleware
        messages = []
        state = {'called': False, 'received': 0}
        events = iter([{'type': 'http.request', 'body': c, 'more_body': n < len(chunks)-1}
                       for n, c in enumerate(chunks)])
        async def receive():
            state['received'] += 1
            return next(events)
        async def send(message):
            messages.append(message)
        async def downstream(scope, receive, send):
            state['called'] = True
            state['body'] = (await receive())['body']
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        middleware = RequestBodyLimitMiddleware(downstream, max_bytes=limit)
        await middleware({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers or []}, receive, send)
        return state, messages

    async def test_exact_boundary_replayed_once(self):
        state, messages = await self.setup_limit([b'1234', b'5678'])
        self.assertTrue(state['called'])
        self.assertEqual(state['body'], b'12345678')
        self.assertEqual(messages[0]['status'], 200)

    async def test_chunked_body_stops_immediately_above_limit(self):
        state, messages = await self.setup_limit([b'1234', b'56789', b'never-read'])
        self.assertFalse(state['called'])
        self.assertEqual(state['received'], 2)
        self.assertEqual(messages[0]['status'], 413)

    async def test_underreported_content_length_cannot_bypass_limit(self):
        state, messages = await self.setup_limit([b'123456789'], headers=[(b'content-length', b'1')])
        self.assertFalse(state['called'])
        self.assertEqual(messages[0]['status'], 413)

    async def test_declared_oversize_rejected_without_reading(self):
        state, messages = await self.setup_limit([], headers=[(b'content-length', b'9')])
        self.assertFalse(state['called'])
        self.assertEqual(state['received'], 0)
        self.assertEqual(messages[0]['status'], 413)


class AdditionalSizeTests(unittest.TestCase):
    def test_invalid_config_does_not_become_unlimited(self):
        from core.body_limit import RequestBodyLimitMiddleware
        for limit in (0, -1, 1048577):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    RequestBodyLimitMiddleware(None, max_bytes=limit)


class ReceiveEdgeTests(ReceiveLimitTests):
    async def test_huge_length_header_is_not_an_integer_conversion_error(self):
        state, messages = await self.setup_limit([], headers=[(b'content-length', b'9' * 5000)])
        self.assertFalse(state['called'])
        self.assertEqual(state['received'], 0)
        self.assertEqual(messages[0]['status'], 413)

    async def test_conflicting_content_length_is_rejected(self):
        state, messages = await self.setup_limit([], headers=[(b'content-length', b'1'), (b'content-length', b'2')])
        self.assertFalse(state['called'])
        self.assertEqual(state['received'], 0)
        self.assertEqual(messages[0]['status'], 400)

    async def test_compressed_request_is_not_decompressed_without_budget(self):
        state, messages = await self.setup_limit([], headers=[(b'content-encoding', b'gzip')])
        self.assertFalse(state['called'])
        self.assertEqual(messages[0]['status'], 415)

    async def test_utf8_counted_in_bytes_across_chunks(self):
        state, messages = await self.setup_limit([b'\xd8', b'\xb3'], limit=1)
        self.assertFalse(state['called'])
        self.assertEqual(messages[0]['status'], 413)

    async def test_zero_padded_content_length_does_not_bypass_limit(self):
        state, messages = await self.setup_limit([b'1234'], headers=[(b'content-length', b'0' * 5000 + b'4')])
        self.assertTrue(state['called'])
        self.assertEqual(messages[0]['status'], 200)

import os
import tempfile
import unittest
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ['PANEL_DATA'] = TEMP.name
os.environ['PANEL_TOKEN'] = 'test-token-not-for-deployment'
from fastapi.testclient import TestClient
import app as panel
from core.upstream import StreamEvent, UpstreamError


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(panel.app)
        self.headers = {'Authorization': 'Bearer test-token-not-for-deployment'}

    def test_private_routes_require_auth(self):
        for path in ['/api/health', '/api/sessions', '/api/pool', '/v1/models', '/docs', '/openapi.json']:
            self.assertIn(self.client.get(path).status_code, (401, 404))

    def test_valid_bearer(self):
        self.assertEqual(self.client.get('/v1/models', headers=self.headers).status_code, 200)

    def test_basic_for_browser(self):
        self.assertEqual(self.client.get('/', auth=('panel', 'test-token-not-for-deployment')).status_code, 200)
        self.assertEqual(self.client.get('/', auth=('panel', 'wrong')).status_code, 401)

    def test_no_configuration_is_closed(self):
        with patch.object(panel.config, 'PANEL_TOKEN', ''):
            self.assertEqual(self.client.get('/api/sessions', headers=self.headers).status_code, 503)

    def test_uid_rotation_not_available(self):
        self.assertEqual(self.client.post('/api/pool/rotate', headers=self.headers).status_code, 404)

    def test_unknown_session_not_replaced(self):
        response = self.client.post('/api/chat', headers=self.headers,
            json={'message': 'test', 'session_id': 'nonexistent'})
        self.assertEqual(response.status_code, 404)

    def test_unsupported_params_rejected(self):
        for field, value in [('tools', []), ('temperature', .3), ('n', 2), ('max_tokens', 1)]:
            r = self.client.post('/v1/chat/completions', headers=self.headers,
                json={'messages': [{'role': 'user', 'content': 'hi'}], field: value})
            self.assertEqual(r.status_code, 422)

    def test_images_rejected(self):
        r = self.client.post('/v1/chat/completions', headers=self.headers,
            json={'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.com'}}]}]})
        self.assertEqual(r.status_code, 422)

    def test_no_history_silently_truncated(self):
        r = self.client.post('/v1/chat/completions', headers=self.headers,
            json={'messages': [{'role': 'system', 'content': 'x' * 2200}, {'role': 'user', 'content': 'hello'}]})
        self.assertEqual(r.status_code, 400)

    def test_invalid_model_and_persona(self):
        for model in ['invented', 'notrack-uncensored:bare']:
            r = self.client.post('/v1/chat/completions', headers=self.headers,
                json={'model': model, 'messages': [{'role': 'user', 'content': 'hello'}]})
            self.assertEqual(r.status_code, 400)

    def test_stream_error_not_success(self):
        async def broken(*args, **kwargs):
            yield StreamEvent('error', code='upstream', text='Unavailable')
        with patch.object(panel.client, 'dispatch_stream', broken):
            r = self.client.post('/v1/chat/completions', headers=self.headers,
                json={'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertIn('"error"', r.text)
        self.assertNotIn('"finish_reason": "stop"', r.text)

    def test_message_only_stream_emits_text_no_fake_usage(self):
        async def final_only(*args, **kwargs):
            yield StreamEvent('message', text='hello', speaker='C')
            yield StreamEvent('done')
        with patch.object(panel.client, 'dispatch_stream', final_only):
            r = self.client.post('/v1/chat/completions', headers=self.headers,
                json={'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertIn('"content": "hello"', r.text)
        self.assertNotIn('"usage"', r.text)
        self.assertNotIn('"type": "session"', r.text)

    def test_session_output_hides_cookie(self):
        s = panel.store.create()
        s.uid = 'sensitive-cookie'
        r = self.client.get('/api/sessions/' + s.id, headers=self.headers)
        self.assertNotIn('sensitive-cookie', r.text)

if __name__ == '__main__':
    unittest.main()


class IntegrityTests(PanelTests):
    def test_concurrent_session_rejected(self):
        session = panel.store.create()
        panel.BUSY.add(session.id)
        try:
            r = self.client.post('/api/chat', headers=self.headers,
                json={'message': 'hi', 'session_id': session.id})
            self.assertEqual(r.status_code, 409)
        finally:
            panel.BUSY.discard(session.id)

    def test_model_or_system_change_rejected(self):
        session = panel.store.create(system='original')
        for change in [{'system': 'new'}, {'speaker': 'A'}]:
            r = self.client.post('/api/chat', headers=self.headers,
                json={'message': 'hi', 'session_id': session.id, **change})
            self.assertEqual(r.status_code, 409)

    def test_origin_rejected(self):
        r = self.client.post('/api/chat', headers={**self.headers, 'Origin': 'https://evil.example'}, json={'message': 'hi'})
        self.assertEqual(r.status_code, 403)

    def test_no_sensitive_uid_in_pool(self):
        with patch.object(panel.pool, 'stats', return_value={'identities': [{'uid': 'secret', 'short': 'masked'}]}):
            r = self.client.get('/api/pool', headers=self.headers)
        self.assertNotIn('secret', r.text)

    def test_stream_failure_not_stored_as_assistant(self):
        session = panel.store.create()
        async def fail(*args, **kwargs):
            yield StreamEvent('delta', text='partial')
            raise UpstreamError('transport_error', 'Unavailable')
        with patch.object(panel.client, 'dispatch_stream', fail):
            r = self.client.post('/api/chat', headers=self.headers,
                json={'message': 'hi', 'session_id': session.id})
        self.assertIn('partial', r.text)
        self.assertIn('transport_error', r.text)
        self.assertNotIn('"type": "done"', r.text)
        self.assertFalse(session.turns)
        self.assertNotIn(session.id, panel.BUSY)


class StreamLifecycleTests(PanelTests):
    def test_mismatch_closes_upstream_generator(self):
        closed = []
        async def mismatch(*args, **kwargs):
            try:
                yield StreamEvent('delta', text='one')
                yield StreamEvent('message', text='different')
            finally:
                closed.append(True)
        with patch.object(panel.client, 'dispatch_stream', mismatch):
            r = self.client.post('/v1/chat/completions', headers=self.headers,
                json={'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertIn('stream_mismatch', r.text)
        self.assertTrue(closed)
        self.assertFalse(panel.BUSY)

    def test_rate_limit_header(self):
        async def limited(*args, **kwargs):
            raise UpstreamError('ratelimit', 'Wait', {'retry_after': 120})
            yield
        with patch.object(panel.client, 'dispatch_stream', limited):
            r = self.client.post('/v1/chat/completions', headers=self.headers,
                json={'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers.get('retry-after'), '120')

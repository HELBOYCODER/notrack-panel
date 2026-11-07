"""Minis/OpenAI client wire-format regressions; no live requests."""
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


class MinisCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.object(panel.config, 'PANEL_TOKEN', 'test-minis-token')
        self.config.start()
        self.http = TestClient(panel.app)
        self.headers = {'Authorization': 'Bearer test-minis-token'}
        self.calls = []
        self.body = {'model': 'notrack-uncensored', 'messages': [
            {'role': 'user', 'content': 'Reply exactly: OK'}],
            'stream': True, 'max_completion_tokens': 128,
            'stream_options': {'include_usage': True}}

        async def complete(*args, **kwargs):
            self.calls.append((args, kwargs))
            yield StreamEvent('message', text='OK', speaker='C')
            yield StreamEvent('done')
        self.upstream = patch.object(panel.client, 'dispatch_stream', complete)
        self.upstream.start()

    def tearDown(self):
        self.upstream.stop()
        self.config.stop()
        self.assertFalse(panel.BUSY)

    def post(self, **changes):
        return self.http.post('/v1/chat/completions', headers=self.headers,
                              json={**self.body, **changes})

    def test_exact_minis_model_test_is_accepted(self):
        r = self.post()
        self.assertEqual(r.status_code, 200, r.text)
        frames = [json.loads(line[6:]) for line in r.text.splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        self.assertTrue(any(f['choices'][0]['delta'].get('content') == 'OK' for f in frames))
        self.assertEqual(frames[-1]['choices'][0]['finish_reason'], 'stop')
        self.assertTrue(r.text.endswith('data: [DONE]\n\n'))
        self.assertNotIn('"usage"', r.text)
        self.assertEqual(len(self.calls), 1)

    def test_no_pretend_native_token_limit_or_usage(self):
        r = self.post()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers['x-notrack-token-limit'], 'advisory-only')
        self.assertEqual(r.headers['x-notrack-usage'], 'unavailable')
        self.assertIn('notrack', r.text)
        self.assertIn('max_completion_tokens', r.text)
        self.assertIn('usage', r.text.lower())

    def test_transport_options_not_forwarded_to_web_backend(self):
        self.assertEqual(self.post().status_code, 200)
        _, kwargs = self.calls[0]
        self.assertNotIn('max_completion_tokens', kwargs)
        self.assertNotIn('stream_options', kwargs)
        self.assertEqual(kwargs['model'], 'C')

    def test_nonstream_accepts_default_client_options(self):
        r = self.post(stream=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['choices'][0]['message']['content'], 'OK')
        self.assertNotIn('usage', r.json())
        self.assertTrue(r.json()['notrack']['warnings'])

    def test_null_client_options_are_harmless(self):
        r = self.post(max_completion_tokens=None, stream_options=None)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn('x-notrack-token-limit', r.headers)

    def test_usage_false_and_empty_options_are_accepted(self):
        for options in ({}, {'include_usage': False}):
            with self.subTest(options=options):
                r = self.post(max_completion_tokens=None, stream_options=options)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertNotIn('"usage"', r.text)

    def test_completion_budget_must_be_positive_strict_integer(self):
        for value in (0, -1, '128', True, 1.5, 1000001):
            with self.subTest(value=value):
                self.assertEqual(self.post(max_completion_tokens=value).status_code, 422)
        self.assertFalse(self.calls)

    def test_stream_options_schema_remains_strict(self):
        for options in ({'unknown': True}, {'include_usage': 'yes'}, {'include_usage': 1}, 'invalid'):
            with self.subTest(options=options):
                self.assertEqual(self.post(stream_options=options).status_code, 422)
        self.assertFalse(self.calls)

    def test_tools_still_rejected_before_upstream(self):
        self.assertEqual(self.post(tools=[]).status_code, 422)
        self.assertFalse(self.calls)

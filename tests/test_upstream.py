import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from core.pool import IdentityPool
from core.upstream import NotrackClient, UpstreamError

UID = "12345678-1234-4234-8234-123456789abc"
OTHER = "87654321-1234-4234-8234-123456789abc"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for byte in self.data:
            yield bytes([byte])


class UpstreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(IdentityPool, "STATE_FILE", Path(self.tmp.name) / "state.json"),
                        patch("core.config.PACE_MIN_INTERVAL", 0.0)]
        for p in self.patches:
            p.start()
        self.calls = []
        self.status = 200
        self.body = b'data: {"type":"message","content":"hello","speaker":"C"}\r\n\r\ndata: {"type":"done"}\r\n\r\n'
        self.pool = IdentityPool()
        self.client = NotrackClient(self.pool)
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    async def asyncTearDown(self):
        await self.client.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def handler(self, req):
        self.calls.append(req)
        if req.url.path == "/chat":
            self.assertNotIn("uid=", req.headers.get("cookie", ""))
            return httpx.Response(200, headers={"set-cookie": f"uid={UID}; Path=/; HttpOnly; Secure"})
        self.assertEqual(req.headers["cookie"], f"uid={UID}")
        return httpx.Response(self.status, headers={"content-type": "text/event-stream", "retry-after": "120"}, stream=Chunks(self.body))

    async def test_bootstrap_crlf_chunks_and_cache(self):
        result = await self.client.complete("hi")
        self.assertEqual(result, {"content": "hello", "chat_id": None, "uid": UID, "speaker": "C"})
        await self.client.complete("again")
        self.assertEqual([r.method for r in self.calls], ["GET", "POST", "POST"])

    async def test_message_only_at_eof(self):
        self.body = b'data: {"type":"message","content":"last"}'
        self.assertEqual((await self.client.complete("hi"))["content"], "last")

    async def test_empty_or_truncated_stream_is_error(self):
        for body in (b"", b'data: {"type":"done"}\n\n', b'data: {"type":"delta","chunk":"partial"}\n\n'):
            self.body = body
            with self.assertRaises(UpstreamError):
                await self.client.complete("hi")

    async def test_sse_rate_is_one_post_and_immediate_cooldown(self):
        self.body = b'data: {"type":"error","code":"ratelimit","content":"SECRET"}\n\n'
        with self.assertRaises(UpstreamError) as caught:
            await self.client.complete("hi")
        self.assertEqual(caught.exception.code, "ratelimit")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertGreaterEqual(self.pool.stats()["identities"][0]["cooldown_in"], 59)
        with self.assertRaises(UpstreamError):
            await asyncio.wait_for(self.client.complete("hi"), 0.1)
        self.assertEqual(sum(r.method == "POST" for r in self.calls), 1)

    async def test_http_rate_retry_after_and_private_errors(self):
        self.status = 429
        self.body = b"SECRET"
        with self.assertRaises(UpstreamError) as caught:
            await self.client.complete("hi")
        self.assertEqual(caught.exception.code, "ratelimit")
        self.assertEqual(caught.exception.detail["retry_after"], 120)
        self.assertGreaterEqual(self.pool.stats()["identities"][0]["cooldown_in"], 119)
        self.assertNotIn("SECRET", str(caught.exception))

    async def test_all_terminal_refusals_raise_without_retry(self):
        for event in ({"type": "instr_refused", "reason": "SECRET"}, {"type": "error", "code": "safety", "content": "SECRET"}):
            self.body = ("data: " + json.dumps(event) + "\n\n").encode()
            before = len(self.calls)
            with self.assertRaises(UpstreamError) as caught:
                await self.client.complete("hi")
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertLessEqual(len(self.calls) - before, 2)

    async def test_legacy_uid_rejected(self):
        with self.assertRaises(UpstreamError) as caught:
            await self.client.complete("hi", pin_uid=OTHER)
        self.assertEqual(caught.exception.code, "identity_mismatch")
        self.assertFalse(any(r.method == "POST" for r in self.calls))

    async def test_one_global_lock_covers_whole_stream_and_cancellation(self):
        started = asyncio.Event()
        released = asyncio.Event()
        active = 0
        maximum = 0
        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(inner):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                started.set()
                try:
                    await released.wait()
                    yield b'data: {"type":"message","content":"ok"}\n\n'
                finally:
                    active -= 1
        def waiting(req):
            if req.method == "GET":
                return self.handler(req)
            self.calls.append(req)
            return httpx.Response(200, stream=Waiting())
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(waiting))
        first = asyncio.create_task(self.client.complete("one"))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.client.complete("two"))
        await asyncio.sleep(0)
        self.assertEqual(sum(r.method == "POST" for r in self.calls), 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        released.set()
        self.assertEqual((await asyncio.wait_for(second, 1))["content"], "ok")
        self.assertEqual(maximum, 1)

    async def test_all_http_errors_and_redirects_are_sanitized(self):
        for status in (302, 400, 401, 403, 500, 502, 503, 504):
            self.status = status
            self.body = b"SECRET"
            before = sum(r.method == "POST" for r in self.calls)
            with self.assertRaises(UpstreamError) as caught:
                await self.client.complete("hi")
            self.assertEqual(caught.exception.detail["status"], status)
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(sum(r.method == "POST" for r in self.calls) - before, 1)

    async def test_missing_or_invalid_server_cookie_never_posts(self):
        for cookie in (None, "uid=bad; Path=/"):
            def bootstrap(req):
                self.calls.append(req)
                return httpx.Response(200, headers={"set-cookie": cookie} if cookie else {})
            await self.client._client.aclose()
            self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(bootstrap))
            with self.assertRaises(UpstreamError):
                await self.client.complete("hi")
        self.assertFalse(any(r.method == "POST" for r in self.calls))
        self.assertIsNone(self.pool.uid)

    async def test_persisted_uid_skips_bootstrap(self):
        await self.pool.bind_uid(UID)
        self.pool = IdentityPool()
        self.client.pool = self.pool
        self.assertEqual((await self.client.complete("hi", pin_uid=UID))["uid"], UID)
        self.assertEqual([r.method for r in self.calls], ["POST"])

    async def test_input_caps_reject_before_any_request(self):
        for kwargs in ({"user_input": "x" * 4001}, {"user_input": "hi", "instructions": "x" * 1981}):
            with self.assertRaises(UpstreamError):
                await self.client.complete(**kwargs)
        self.assertEqual(self.calls, [])

    async def test_retry_after_is_bounded_and_bad_values_are_safe(self):
        for value, expected in ((None, 60), ("nonsense", 60), ("-1", 60), ("nan", 60), ("inf", 60), ("999999", 3600)):
            self.assertEqual(self.client._retry_after(value), expected)

    async def test_delta_done_multiline_and_malformed_events(self):
        self.body = b': ping\r\nevent: delta\r\ndata: {"chunk":\r\ndata: "hello"}\r\n\r\ndata: {"type":"done"}\r\n\r\n'
        self.assertEqual((await self.client.complete("hi"))["content"], "hello")
        for body in (b'data: SECRET\n\n', b'data: []\n\n'):
            self.body = body
            with self.assertRaises(UpstreamError):
                await self.client.complete("hi")

    async def test_transport_failure_not_retried(self):
        def broken(req):
            if req.method == "GET":
                return self.handler(req)
            self.calls.append(req)
            raise httpx.ReadError("SECRET", request=req)
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(broken))
        with self.assertRaises(UpstreamError) as caught:
            await self.client.complete("hi")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(sum(r.method == "POST" for r in self.calls), 1)


class TerminalRequirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_message_without_done_is_explicitly_complete(self):
        # A final message is accepted as the protocol's terminal completion.
        self.assertEqual(NotrackClient._map_event({'type': 'message', 'content': 'ok'}).type, 'message')

    async def test_event_size_is_bounded(self):
        uid = UID
        with self.assertRaises(UpstreamError):
            NotrackClient._decode_event(['{"type":"message","content":"' + 'x' * 1048577 + '"}'], None, uid)

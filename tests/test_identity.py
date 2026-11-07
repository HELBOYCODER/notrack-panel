import asyncio
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.pool import IdentityPool

UID = "12345678-1234-4234-8234-123456789abc"
OTHER = "87654321-1234-4234-8234-123456789abc"


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "upstream_state.json"
        self.patch = patch.object(IdentityPool, "STATE_FILE", self.state)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    async def test_no_mint_and_server_uid_persists_private(self):
        pool = IdentityPool(size=100)
        self.assertEqual(pool.stats()["count"], 0)
        await pool.bind_uid(UID)
        self.assertEqual((await pool.acquire()).uid, UID)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        self.assertEqual(IdentityPool().uid, UID)
        with self.assertRaises(Exception):
            await pool.bind_uid(OTHER)
        with self.assertRaises(Exception):
            await pool.acquire_uid(OTHER)
        with self.assertRaises(Exception):
            await pool.rotate_all()

    async def test_cooldown_is_immediate_and_survives_restart(self):
        pool = IdentityPool()
        await pool.bind_uid(UID)
        ident = await pool.acquire()
        await pool.report_429(ident)
        self.assertGreaterEqual(ident.wait_for(), 59)
        self.assertEqual(pool.stats()["ready"], 0)
        with self.assertRaises(Exception):
            await asyncio.wait_for(pool.acquire(), timeout=0.1)
        restored = IdentityPool()
        self.assertEqual(restored.uid, UID)
        self.assertEqual(restored.stats()["ready"], 0)

    async def test_invalid_uid_and_corrupt_state_fail_closed(self):
        with self.assertRaises(Exception):
            await IdentityPool().bind_uid("not-a-uuid")
        self.state.write_text(json.dumps({"uid": "bad"}))
        with self.assertRaises(Exception):
            IdentityPool()

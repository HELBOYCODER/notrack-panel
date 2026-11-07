"""Anonymous identity pool.

The upstream assigns every visitor an HttpOnly `uid` cookie and meters the free
tier against it. A fresh uid therefore behaves like a fresh visitor — with one
caveat found during recon: the limiter also counts from the IP, so rotation
raises the ceiling but does not remove it. This pool rotates uids, tracks
cooldowns per identity, and exposes an adaptive global pace so callers never
have to think about it.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import config


@dataclass
class Identity:
    uid: str
    created_at: float = field(default_factory=time.time)
    cooldown_until: float = 0.0
    requests: int = 0
    failures: int = 0
    last_used: float = 0.0

    def ready(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) >= self.cooldown_until

    def wait_for(self, now: Optional[float] = None) -> float:
        return max(0.0, self.cooldown_until - (now or time.time()))


class IdentityPool:
    """Round-robin uid pool with per-identity cooldowns and a global pace."""

    STATE_FILE = config.DATA_DIR / "identities.json"

    def __init__(self, size: int = 12) -> None:
        self.size = size
        self._lock = asyncio.Lock()
        self._ids: dict[str, Identity] = {}
        self._order: list[str] = []
        self._cursor = 0
        self._last_send = 0.0

        # adaptive pacing shared by all callers
        self.pace = config.PACE_MIN_INTERVAL
        self._consecutive_429 = 0
        self._consecutive_ok = 0

        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            raw = json.loads(self.STATE_FILE.read_text())
        except Exception:
            raw = None
        if raw and isinstance(raw.get("identities"), list):
            for item in raw["identities"]:
                try:
                    ident = Identity(**item)
                except Exception:
                    continue
                self._ids[ident.uid] = ident
                self._order.append(ident.uid)
        while len(self._order) < self.size:
            self._spawn()

    def _save(self) -> None:
        try:
            payload = {"identities": [asdict(i) for i in self._ids.values()]}
            tmp = self.STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1))
            tmp.replace(self.STATE_FILE)
        except Exception:
            pass

    def _spawn(self) -> Identity:
        ident = Identity(uid=str(uuid.uuid4()))
        self._ids[ident.uid] = ident
        self._order.append(ident.uid)
        return ident

    # ------------------------------------------------------------ acquisition
    async def acquire(self) -> Identity:
        """Return an identity that is currently outside its cooldown.

        If every identity is cooling down we wait for the one that frees up
        first instead of hammering the upstream.
        """
        while True:
            async with self._lock:
                now = time.time()
                n = len(self._order)
                for step in range(n):
                    idx = (self._cursor + step) % n
                    ident = self._ids[self._order[idx]]
                    if ident.ready(now):
                        self._cursor = (idx + 1) % n
                        ident.last_used = now
                        ident.requests += 1
                        self._save()
                        return ident
                # nothing ready — find the soonest
                soonest = min(self._ids[u] for u in self._order)
                wait = soonest.wait_for(now)
            await asyncio.sleep(min(max(wait, 0.4), config.PACE_MAX_INTERVAL))

    async def pace_gate(self) -> None:
        """Block until the global inter-request interval has elapsed."""
        while True:
            async with self._lock:
                now = time.time()
                delta = now - self._last_send
                if delta >= self.pace:
                    self._last_send = now
                    return
                wait = self.pace - delta
            await asyncio.sleep(wait)

    async def acquire_uid(self, uid: str) -> Identity:
        """Return the *specific* identity that owns a chat, creating it if unknown.

        Continuity matters more than throughput here: a chat only has history on
        the uid that created it, so we wait out that identity's cooldown instead
        of borrowing a fresh one.
        """
        while True:
            async with self._lock:
                ident = self._ids.get(uid)
                if ident is None:
                    ident = Identity(uid=uid)
                    self._ids[uid] = ident
                    self._order.append(uid)
                    self._save()
                now = time.time()
                if ident.ready(now):
                    ident.last_used = now
                    ident.requests += 1
                    self._save()
                    return ident
                wait = ident.wait_for(now)
            await asyncio.sleep(min(max(wait, 0.4), config.PACE_MAX_INTERVAL))

    # ------------------------------------------------------------ feedback
    async def report_ok(self, ident: Identity) -> None:
        async with self._lock:
            self._consecutive_429 = 0
            self._consecutive_ok += 1
            if self._consecutive_ok >= 6 and self.pace > config.PACE_MIN_INTERVAL:
                self.pace = max(config.PACE_MIN_INTERVAL, self.pace * 0.9)
                self._consecutive_ok = 0
            self._save()

    async def report_429(self, ident: Identity) -> None:
        async with self._lock:
            self._consecutive_ok = 0
            self._consecutive_429 += 1
            self.pace = min(config.PACE_MAX_INTERVAL, self.pace * 1.5)
            # cool this identity down with exponential jitter
            cooldown = min(60.0, 4.0 * (1.7 ** min(self._consecutive_429, 5)))
            ident.cooldown_until = time.time() + cooldown
            ident.failures += 1
            self._save()

    async def report_error(self, ident: Identity) -> None:
        async with self._lock:
            ident.failures += 1
            self._save()

    # ------------------------------------------------------------ inspection
    def stats(self) -> dict:
        now = time.time()
        rows = []
        for uid in self._order:
            i = self._ids[uid]
            rows.append({
                "uid": i.uid,
                "short": i.uid[:8],
                "requests": i.requests,
                "failures": i.failures,
                "ready": i.ready(now),
                "cooldown_in": round(i.wait_for(now), 1),
            })
        return {
            "count": len(self._order),
            "pace": round(self.pace, 2),
            "ready": sum(1 for r in rows if r["ready"]),
            "identities": rows,
        }

    async def rotate_all(self) -> None:
        """Discard every identity and start clean."""
        async with self._lock:
            self._ids.clear()
            self._order.clear()
            self._cursor = 0
            while len(self._order) < self.size:
                self._spawn()
            self._save()
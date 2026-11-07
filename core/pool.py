"""One server-issued identity; never mint, rotate, or reset quota state.

ponytail: intentionally one process, one worker, one operator. The legacy pool
API remains, but size is ignored and unknown uids are rejected.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import config


class IdentityError(Exception):
    def __init__(self, code: str, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass
class Identity:
    uid: str
    created_at: float = field(default_factory=time.time)
    cooldown_until: float = 0.0
    requests: int = 0
    failures: int = 0
    last_used: float = 0.0

    def ready(self, now: Optional[float] = None) -> bool:
        return (time.time() if now is None else now) >= self.cooldown_until

    def wait_for(self, now: Optional[float] = None) -> float:
        return max(0.0, self.cooldown_until - (time.time() if now is None else now))


class IdentityPool:
    STATE_FILE = config.DATA_DIR / "upstream_state.json"

    def __init__(self, size: int = 1) -> None:
        self.size = 1  # legacy argument deliberately ignored
        self._lock = asyncio.Lock()
        self._identity: Optional[Identity] = None
        self._last_send = 0.0
        self.pace = config.PACE_MIN_INTERVAL
        self._load()

    @property
    def uid(self) -> Optional[str]:
        return self._identity.uid if self._identity else None

    @staticmethod
    def _validate_uid(uid: str) -> str:
        try:
            if not isinstance(uid, str) or str(uuid.UUID(uid)) != uid.lower():
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise IdentityError("invalid_identity", "Invalid upstream identity") from None
        return uid

    def _load(self) -> None:
        try:
            raw = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
            ident = Identity(**raw["identity"])
            self._validate_uid(ident.uid)
            times = (ident.created_at, ident.cooldown_until, ident.last_used)
            if not all(isinstance(t, (float, int)) and math.isfinite(t) and t >= 0 for t in times):
                raise ValueError
            if not all(isinstance(n, int) and n >= 0 for n in (ident.requests, ident.failures)):
                raise ValueError
            os.chmod(self.STATE_FILE, 0o600)
            self._identity = ident
            self._last_send = ident.last_used
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError, IdentityError):
            # Fail closed: corrupt state must not silently create a new visitor.
            raise IdentityError("identity_state", "Cannot load upstream identity state") from None

    def _save(self) -> None:
        if self._identity is None:
            return
        tmp = None
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".upstream-", dir=self.STATE_FILE.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"identity": asdict(self._identity)}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.STATE_FILE)
        except OSError:
            raise IdentityError("identity_state", "Cannot persist upstream identity state") from None
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass

    async def bind_uid(self, uid: str) -> Identity:
        """Bind only the validated cookie received by the client from GET /chat."""
        self._validate_uid(uid)
        async with self._lock:
            if self._identity is not None and uid != self.uid:
                raise IdentityError("identity_mismatch", "Session does not match the stable upstream identity")
            if self._identity is None:
                self._identity = Identity(uid=uid)
            self._save()
            return self._identity

    def _check_ready(self) -> Identity:
        if self._identity is None:
            raise IdentityError("identity_missing", "Upstream identity has not been initialized")
        wait = self._identity.wait_for()
        if wait > 0:
            raise IdentityError("ratelimit", "Upstream cooldown is active", math.ceil(wait))
        return self._identity

    async def acquire(self) -> Identity:
        async with self._lock:
            ident = self._check_ready()
            ident.requests += 1
            self._save()
            return ident

    async def acquire_uid(self, uid: str) -> Identity:
        if uid != self.uid:
            raise IdentityError("identity_mismatch", "Session does not match the stable upstream identity")
        return await self.acquire()

    async def pace_gate(self) -> None:
        """Normal pacing may wait; upstream cooldown always errors immediately."""
        while True:
            async with self._lock:
                ident = self._check_ready()
                now = time.time()
                wait = self.pace - (now - self._last_send)
                if wait <= 0:
                    self._last_send = ident.last_used = now
                    self._save()
                    return
            await asyncio.sleep(wait)

    def _check_owner(self, ident: Identity) -> None:
        if ident is not self._identity:
            raise IdentityError("identity_mismatch", "Unknown upstream identity")

    async def report_ok(self, ident: Identity) -> None:
        async with self._lock:
            self._check_owner(ident)
            self._save()  # never reset cooldown or accelerate after success

    async def report_429(self, ident: Identity, retry_after: float = 60.0) -> None:
        async with self._lock:
            self._check_owner(ident)
            delay = max(config.RATE_LIMIT_COOLDOWN, min(config.MAX_RETRY_AFTER, retry_after))
            ident.cooldown_until = max(ident.cooldown_until, time.time() + delay)
            ident.failures += 1
            self._save()

    async def report_error(self, ident: Identity) -> None:
        async with self._lock:
            self._check_owner(ident)
            ident.failures += 1
            self._save()

    def stats(self) -> dict:
        rows = []
        if self._identity:
            ident = self._identity
            rows.append({"uid": ident.uid, "short": ident.uid[:8],
                         "requests": ident.requests, "failures": ident.failures,
                         "ready": ident.ready(), "cooldown_in": round(ident.wait_for(), 1)})
        return {"count": len(rows), "ready": sum(int(r["ready"]) for r in rows),
                "pace": round(self.pace, 2), "identities": rows}

    async def rotate_all(self) -> None:
        raise IdentityError("rotation_disabled", "Upstream identity rotation is disabled")

"""Conversation store — keeps panel-side sessions so the OpenAI shim can be stateless."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import config


@dataclass
class Turn:
    role: str
    content: str
    speaker: str = ""
    ts: float = field(default_factory=time.time)


@dataclass
class Session:
    id: str
    chat_id: Optional[str] = None          # upstream chat id
    uid: Optional[str] = None               # identity that owns chat_id
    speaker: str = "C"
    persona: str = "normal"
    system: str = ""                        # becomes `instructions` on first turn
    turns: list[Turn] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class SessionStore:
    FILE = config.DATA_DIR / "sessions.json"

    def __init__(self, limit: int = 500) -> None:
        self.limit = limit
        self._sessions: dict[str, Session] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.FILE.read_text())
        except Exception:
            return
        for item in raw.get("sessions", []):
            try:
                sess = Session(
                    id=item["id"], chat_id=item.get("chat_id"),
                    uid=item.get("uid"),
                    speaker=item.get("speaker", "C"),
                    persona=item.get("persona", "normal"),
                    system=item.get("system", ""),
                    turns=[Turn(**t) for t in item.get("turns", [])],
                    created=item.get("created", time.time()),
                    updated=item.get("updated", time.time()),
                )
                self._sessions[sess.id] = sess
            except Exception:
                continue

    def save(self) -> None:
        try:
            ordered = sorted(self._sessions.values(), key=lambda s: s.updated, reverse=True)
            keep = ordered[: self.limit]
            self._sessions = {s.id: s for s in keep}
            payload = {"sessions": [s.to_dict() for s in keep]}
            tmp = self.FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1))
            tmp.replace(self.FILE)
        except Exception:
            pass

    # ------------------------------------------------------------------ api
    def create(self, speaker: str = "C", persona: str = "normal", system: str = "") -> Session:
        sess = Session(id=f"nt-{uuid.uuid4().hex[:16]}", speaker=speaker,
                       persona=persona, system=system)
        self._sessions[sess.id] = sess
        self.save()
        return sess

    def get(self, sid: str) -> Optional[Session]:
        return self._sessions.get(sid)

    def list(self) -> list[dict]:
        out = []
        for s in sorted(self._sessions.values(), key=lambda x: x.updated, reverse=True):
            out.append({
                "id": s.id,
                "chat_id": s.chat_id,
                "speaker": s.speaker,
                "persona": s.persona,
                "turns": len(s.turns),
                "title": next((t.content[:60] for t in s.turns if t.role == "user"), "(empty)"),
                "updated": s.updated,
            })
        return out

    def delete(self, sid: str) -> bool:
        if sid in self._sessions:
            del self._sessions[sid]
            self.save()
            return True
        return False

    def append(self, sess: Session, role: str, content: str, speaker: str = "") -> None:
        sess.turns.append(Turn(role=role, content=content, speaker=speaker))
        sess.updated = time.time()
        self.save()
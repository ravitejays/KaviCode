"""Session persistence.

Each session lives in its own directory under the platform data dir:
    sessions/<id>/meta.json         - session metadata
    sessions/<id>/messages.jsonl    - one JSON message per line (append-only)
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from kavi.config.loader import sessions_dir
from kavi.messages import Message
from kavi.session.models import (
    LoadedSession,
    SessionMeta,
    message_from_dict,
    message_to_dict,
)

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def generate_id(size: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(size))


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or sessions_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, session_id: str) -> Path:
        return self.root / session_id

    # -- creation / writing ------------------------------------------------------

    def create(self, cwd: Path, provider: str, model: str) -> SessionMeta:
        session_id = generate_id()
        now = _now()
        meta = SessionMeta(
            id=session_id,
            created_at=now,
            updated_at=now,
            cwd=str(cwd),
            provider=provider,
            model=model,
        )
        d = self._dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        self._write_meta(meta)
        (d / "messages.jsonl").touch()
        return meta

    def append(self, meta: SessionMeta, message: Message) -> None:
        path = self._dir(meta.id) / "messages.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(message_to_dict(message)) + "\n")
        meta.message_count += 1
        meta.updated_at = _now()
        if not meta.title and message.role == "user":
            meta.title = message.text().strip().replace("\n", " ")[:80]
        self._write_meta(meta)

    def _write_meta(self, meta: SessionMeta) -> None:
        path = self._dir(meta.id) / "meta.json"
        path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    # -- reading -----------------------------------------------------------------

    def list(self, limit: int = 50) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for d in self.root.iterdir():
            meta_file = d / "meta.json"
            if meta_file.is_file():
                try:
                    metas.append(SessionMeta.from_dict(json.loads(meta_file.read_text())))
                except (json.JSONDecodeError, OSError, TypeError):
                    continue
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas[:limit]

    def load(self, session_id: str) -> LoadedSession | None:
        d = self._dir(session_id)
        meta_file = d / "meta.json"
        if not meta_file.is_file():
            return None
        meta = SessionMeta.from_dict(json.loads(meta_file.read_text()))
        messages: list[Message] = []
        msg_file = d / "messages.jsonl"
        if msg_file.is_file():
            for line in msg_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    messages.append(message_from_dict(json.loads(line)))
        return LoadedSession(meta=meta, messages=messages)

    def latest(self) -> SessionMeta | None:
        sessions = self.list(limit=1)
        return sessions[0] if sessions else None

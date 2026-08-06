"""临时会话存储（仅内存）。

缓存：本地 response_id、上游 response_id、显式上下文历史（含 assistant
tool_calls 与 reasoning_content）、模型与会话信息。容量与过期策略防止
无限增长；进程退出后全部清除。
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    local_id: str
    upstream_id: str
    model: str
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.updated_at = time.monotonic()


class SessionStore:
    def __init__(self, max_sessions: int = 100, ttl_seconds: float = 1800.0, max_history_items: int = 200):
        self._sessions: dict[str, Session] = {}
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self.max_history_items = max_history_items
        self._lock = threading.RLock()

    def create(
        self,
        local_id: str,
        upstream_id: str,
        model: str,
        history: list[dict[str, Any]],
    ) -> Session:
        with self._lock:
            session = Session(local_id=local_id, upstream_id=upstream_id, model=model, history=list(history)[-self.max_history_items:])
            self._sessions[local_id] = session
            self._evict()
            return session

    def get(self, local_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(local_id)
            if session is None:
                return None
            if time.monotonic() - session.updated_at > self.ttl_seconds:
                self._sessions.pop(local_id, None)
                return None
            return session

    def update(self, local_id: str, upstream_id: str, history: list[dict[str, Any]]) -> Session | None:
        with self._lock:
            session = self._sessions.get(local_id)
            if session is None:
                return None
            session.upstream_id = upstream_id
            session.history = list(history)[-self.max_history_items:]
            session.touch()
            return session

    def prune(self) -> None:
        with self._lock:
            now = time.monotonic()
            expired = [sid for sid, s in self._sessions.items() if now - s.updated_at > self.ttl_seconds]
            for sid in expired:
                self._sessions.pop(sid, None)

    def size(self) -> int:
        with self._lock:
            return len(self._sessions)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _evict(self) -> None:
        with self._lock:
            self.prune()
            while len(self._sessions) > self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
                self._sessions.pop(oldest.local_id, None)

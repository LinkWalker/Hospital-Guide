"""会话存储：内存实现（默认）与 Redis 实现（可选）。

接口统一，配置 session_store=redis 时切换。内存实现在单机演示足够；
上线多实例时应使用 Redis 保证多轮状态共享。
"""
import json
import threading
from typing import Optional

from ..schemas.models import SessionState

LOCK = threading.Lock()
_MEM: dict = {}


class SessionStore:
    """会话存储抽象基类。"""

    def save(self, state: SessionState) -> None:
        raise NotImplementedError

    def load(self, session_id: str) -> Optional[SessionState]:
        raise NotImplementedError


class MemorySessionStore(SessionStore):
    """进程内存储：单机演示用。"""

    def save(self, state: SessionState) -> None:
        with LOCK:
            _MEM[state.session_id] = state.model_dump()

    def load(self, session_id: str) -> Optional[SessionState]:
        with LOCK:
            data = _MEM.get(session_id)
        return SessionState.model_validate(data) if data else None


class RedisSessionStore(SessionStore):
    """Redis 存储：上线多实例用。需先安装 redis 且 redis_url 可达。"""

    def __init__(self, redis_url: str, ttl: int = 3600):
        import redis  # 延迟导入：未安装时内存模式不受影响
        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl
        self._prefix = "triage:session:"

    def save(self, state: SessionState) -> None:
        key = self._prefix + state.session_id
        self._r.set(key, json.dumps(state.model_dump(), ensure_ascii=False), ex=self._ttl)

    def load(self, session_id: str) -> Optional[SessionState]:
        key = self._prefix + session_id
        raw = self._r.get(key)
        if not raw:
            return None
        return SessionState.model_validate(json.loads(raw))


def build_session_store(settings) -> SessionStore:
    """根据配置构建会话存储。"""
    if settings.session_store == "redis":
        return RedisSessionStore(settings.redis_url)
    return MemorySessionStore()

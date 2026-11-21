"""会话历史存储（SQLite）。

设计目标：
- 让“过往对话”在服务重启后仍然存在；
- 只保存分诊所需的最小字段，支持单条删除 / 全部清空 / 过期清理；
- 保存 SessionState 快照，支持从历史恢复多轮上下文继续对话。

隐私说明：
- 历史记录可能包含症状描述，属于敏感信息；默认保留 30 天，可在 .env 关闭或调整；
- 前端必须提供删除入口；部署时应使用 HTTPS，并限制数据库文件访问权限；
- 不保存姓名、手机号、身份证号等身份信息（前端也不收集）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import DB_PATH

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_history (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'collecting',
    department     TEXT,
    urgent_level   TEXT,
    message_count  INTEGER NOT NULL DEFAULT 0,
    messages       TEXT NOT NULL,
    state          TEXT,
    result         TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_updated ON conversation_history(updated_at DESC);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _make_title(first_user_message: str, fallback: str = "未命名会话") -> str:
    text = " ".join(str(first_user_message or "").split())
    if not text:
        return fallback
    return text[:28] + ("…" if len(text) > 28 else "")


class HistoryStore:
    """会话历史读写。每个方法独立连接，避免跨线程共享 sqlite3 连接。"""

    def __init__(
        self,
        db_path: Optional[str] = None,
        retention_days: int = 30,
        max_items: int = 200,
    ):
        self.db_path = os.path.abspath(db_path or DB_PATH)
        self.retention_days = max(0, int(retention_days))
        self.max_items = max(1, int(max_items))
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.ensure_schema()

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_CREATE_SQL)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def save(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        title: Optional[str] = None,
        status: str = "collecting",
        state: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        department: Optional[str] = None,
        urgent_level: Optional[str] = None,
    ) -> None:
        """新增或更新一条会话历史（按 session_id upsert）。"""
        if not session_id or not messages:
            return
        cleaned: List[Dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "")).strip()
            content = str(msg.get("content", ""))
            if role not in ("user", "assistant") or not content:
                continue
            item = {"role": role, "content": content}
            # 保留改口等审计字段，但不把内部大对象塞进展示消息
            if msg.get("correction"):
                item["correction"] = True
            if msg.get("current_complaint"):
                item["current_complaint"] = str(msg["current_complaint"])
            cleaned.append(item)
        if not cleaned:
            return

        first_user = next((m["content"] for m in cleaned if m["role"] == "user"), "")
        now = _now()
        messages_json = json.dumps(cleaned, ensure_ascii=False)
        state_json = json.dumps(state, ensure_ascii=False) if state else None
        result_json = json.dumps(result, ensure_ascii=False) if result else None
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO conversation_history
                    (id, title, status, department, urgent_level, message_count,
                     messages, state, result, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    status=excluded.status,
                    department=excluded.department,
                    urgent_level=excluded.urgent_level,
                    message_count=excluded.message_count,
                    messages=excluded.messages,
                    state=excluded.state,
                    result=excluded.result,
                    updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    title or _make_title(first_user),
                    status,
                    department,
                    urgent_level,
                    len(cleaned),
                    messages_json,
                    state_json,
                    result_json,
                    now,
                    now,
                ),
            )
            conn.commit()
            self._trim(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def list(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        self._cleanup()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, title, status, department, urgent_level,
                       message_count, created_at, updated_at
                FROM conversation_history
                ORDER BY updated_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count(self) -> int:
        self._cleanup()
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(1) FROM conversation_history").fetchone()[0])
        finally:
            conn.close()

    def get(self, history_id: str) -> Optional[Dict[str, Any]]:
        if not history_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM conversation_history WHERE id = ?", (history_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        data = dict(row)
        data["messages"] = _loads(data.get("messages"), [])
        data["result"] = _loads(data.get("result"), None)
        data["state"] = _loads(data.get("state"), None)
        return data

    # ------------------------------------------------------------------
    def delete(self, history_id: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM conversation_history WHERE id = ?", (history_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def clear(self) -> int:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM conversation_history")
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def _trim(self, conn: sqlite3.Connection) -> None:
        """只保留最近 max_items 条，防止课程演示库无限膨胀。"""
        conn.execute(
            """
            DELETE FROM conversation_history
            WHERE id NOT IN (
                SELECT id FROM conversation_history
                ORDER BY updated_at DESC, rowid DESC
                LIMIT ?
            )
            """,
            (self.max_items,),
        )
        conn.commit()

    def _cleanup(self) -> None:
        if self.retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute("DELETE FROM conversation_history WHERE updated_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def build_history_store(settings) -> Optional[HistoryStore]:
    """根据配置构建历史存储；关闭时返回 None，调用方需判空。"""
    if not getattr(settings, "history_enabled", True):
        return None
    return HistoryStore(
        retention_days=getattr(settings, "history_retention_days", 30),
        max_items=getattr(settings, "history_max_items", 200),
    )

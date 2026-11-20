"""会话历史存储与 API 测试。"""
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.llm_gateway import LLMGateway
from app.db.history_store import HistoryStore
from app.db.session_store import MemorySessionStore
from app.main import app
from app.modules.dialog import DialogStateMachine
from app.modules.nlu import build_extractor
from app.modules.orchestrator import Orchestrator
from app.modules.rag import build_retriever
from app.modules.triage import build_interceptor


def _messages(*pairs):
    out = []
    for role, content in pairs:
        out.append({"role": role, "content": content})
    return out


@pytest.fixture
def tmp_dir():
    """项目内临时目录：避免 pytest 默认临时目录在受限环境不可写。"""
    path = Path(f"history-test-{uuid.uuid4().hex[:10]}")
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_history_store_crud_and_trim(tmp_dir):
    store = HistoryStore(db_path=str(tmp_dir / "history.db"), retention_days=30, max_items=2)

    for i in range(3):
        store.save(
            session_id=f"s-{i}",
            messages=_messages(("user", f"肚子疼{i}"), ("assistant", "请问部位？")),
            status="collecting",
            state={"session_id": f"s-{i}"},
            result={"collected": {}},
        )

    items = store.list()
    assert len(items) == 2
    assert items[0]["id"] == "s-2"  # 最新的在最前

    detail = store.get("s-2")
    assert detail is not None
    assert detail["messages"][0]["content"] == "肚子疼2"
    assert detail["state"]["session_id"] == "s-2"
    assert detail["result"] == {"collected": {}}

    assert store.count() == 2
    assert store.delete("s-2") is True
    assert store.get("s-2") is None
    assert store.delete("missing") is False
    store.clear()
    assert store.count() == 0


def test_history_title_is_truncated(tmp_dir):
    store = HistoryStore(db_path=str(tmp_dir / "history.db"))
    long_text = "我" * 100
    store.save(session_id="s-title", messages=_messages(("user", long_text), ("assistant", "好的")))
    item = store.list()[0]
    assert len(item["title"]) <= 29
    assert item["title"].endswith("…")


def _build_orchestrator(history):
    llm = LLMGateway("mock")
    return Orchestrator(
        store=MemorySessionStore(),
        extractor=build_extractor(llm),
        interceptor=build_interceptor(),
        dialog=DialogStateMachine(3),
        retriever=build_retriever(use_vector=False),
        llm=llm,
        history=history,
    )


@pytest.fixture
def history_client(tmp_dir, monkeypatch):
    monkeypatch.setattr("app.db.session_store._MEM", {})
    store = HistoryStore(db_path=str(tmp_dir / "api-history.db"))
    orch = _build_orchestrator(store)
    monkeypatch.setattr(routes, "get_history_store", lambda: store)
    monkeypatch.setattr(routes, "get_orchestrator", lambda: orch)
    with TestClient(app) as c:
        yield c, store


def test_history_api_end_to_end(history_client):
    client, store = history_client

    # 第一轮：追问
    r1 = client.post("/api/v1/chat", json={"session_id": "h-1", "message": "我胸口闷"})
    assert r1.status_code == 200
    assert r1.json()["type"] == "question"

    items = client.get("/api/v1/history").json()
    assert items["total"] == 1
    assert items["items"][0]["id"] == "h-1"
    assert items["items"][0]["status"] == "collecting"

    detail = client.get("/api/v1/history/h-1").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert "state" not in detail  # 不暴露内部会话状态

    # 补齐信息出结果
    for text in ("已经三天了", "闷痛，成年人"):
        r = client.post("/api/v1/chat", json={"session_id": "h-1", "message": text})
        assert r.status_code == 200
        if r.json()["type"] == "result":
            break
    detail = client.get("/api/v1/history/h-1").json()
    assert detail["status"] == "ready"
    assert detail["department"] == "心血管内科"
    assert detail["result"]["recommended_department"] == "心血管内科"

    # 恢复历史会话：状态应回到内存 store
    restored = client.post("/api/v1/history/h-1/restore")
    assert restored.status_code == 200
    body = restored.json()
    assert body["session_id"] == "h-1"
    assert body["messages"]

    # 删除
    assert client.delete("/api/v1/history/h-1").status_code == 204
    assert client.get("/api/v1/history/h-1").status_code == 404


def test_history_api_clear_and_404(history_client):
    client, _ = history_client
    client.post("/api/v1/chat", json={"session_id": "h-a", "message": "头痛"})
    client.post("/api/v1/chat", json={"session_id": "h-b", "message": "牙痛"})
    assert client.get("/api/v1/history").json()["total"] == 2
    assert client.delete("/api/v1/history").status_code == 204
    assert client.get("/api/v1/history").json()["total"] == 0
    assert client.post("/api/v1/history/nope/restore").status_code == 404
    assert client.delete("/api/v1/history/nope").status_code == 404


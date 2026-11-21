"""编排器端到端测试（mock LLM）：急症、追问、结果三条路径。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session_store import MemorySessionStore
from app.core.llm_gateway import LLMGateway
from app.modules.nlu import build_extractor
from app.modules.triage import build_interceptor
from app.modules.dialog import DialogStateMachine
from app.modules.rag import build_retriever
from app.modules.orchestrator import Orchestrator


def _orch():
    # 测试环境强制 mock 网关，不依赖 .env 是否配置了真实 API Key
    llm = LLMGateway(provider="mock", base_url="", api_key="", model="")
    return Orchestrator(
        store=MemorySessionStore(),
        extractor=build_extractor(llm),
        interceptor=build_interceptor(),
        dialog=DialogStateMachine(),
        retriever=build_retriever(use_vector=False),
        llm=llm,
    )


def test_urgent_path():
    orch = _orch()
    resp = orch.chat("s-urgent", "我胸痛伴出冷汗")
    assert resp.type == "urgent"
    assert "120" in resp.message


def test_followup_then_result():
    orch = _orch()
    r1 = orch.chat("s-multi", "我胸口闷")
    assert r1.type == "question"
    r2 = orch.chat("s-multi", "闷，已经三天了")
    assert r2.type in ("question", "result")
    # 补全 nature 后应出结果
    if r2.type == "question":
        r3 = orch.chat("s-multi", "是闷痛感，我成年男性")
        assert r3.type == "result"


def test_direct_result():
    orch = _orch()
    resp = orch.chat("s-full", "我胸口闷痛，喘不上气，持续三天，我是成年人")
    # 足够信息可能直接出结果，或先追问伴随症状
    assert resp.type in ("question", "result")


def test_session_persistence():
    orch = _orch()
    orch.chat("s-p", "肚子疼")
    state = orch.store.load("s-p")
    assert state is not None
    assert len(state.history) >= 1

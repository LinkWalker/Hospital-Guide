"""规则命中与人群识别回归测试（修复：原始文本匹配 + 成年人识别）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.rag import build_retriever
from app.modules.nlu import build_extractor
from app.schemas.models import SymptomInfo


def test_rule_hit_via_raw_text():
    """用户原话"胸口闷"应命中心血管内科（用原始文本匹配连续关键词）。"""
    retriever = build_retriever()
    symptom = SymptomInfo(part="胸部", nature="闷")
    hit = retriever.rule.search(symptom, raw_text="我胸口闷")
    assert hit is not None
    assert hit["recommended_dept"] == "心血管内科"


def test_rule_hit_stomach():
    retriever = build_retriever()
    hit = retriever.rule.search(SymptomInfo(part="腹部"), raw_text="我肚子疼还拉肚子")
    assert hit is not None
    assert hit["recommended_dept"] == "消化内科"


def test_adult_population_recognized():
    """"成年人"应被识别进人群字段。"""
    extractor = build_extractor()
    info = extractor.extract("我肚子疼，我是成年人")
    assert info.population == "成年人"


def test_rule_hit_across_turns():
    """多轮对话：关键词出现在历史首轮，末轮匹配时应基于整段累计文本命中。"""
    from app.db.session_store import MemorySessionStore
    from app.core.llm_gateway import LLMGateway
    from app.modules.triage import build_interceptor
    from app.modules.dialog import DialogStateMachine
    from app.modules.orchestrator import Orchestrator

    llm = LLMGateway(provider="mock", base_url="", api_key="", model="")
    orch = Orchestrator(
        store=MemorySessionStore(),
        extractor=build_extractor(llm),
        interceptor=build_interceptor(),
        dialog=DialogStateMachine(),
        retriever=build_retriever(use_vector=False),
        llm=llm,
    )
    orch.chat("reg-multi", "我胸口闷")
    orch.chat("reg-multi", "闷，三天了")
    resp = orch.chat("reg-multi", "是闷痛，成年男性")
    assert resp.type == "result"
    assert resp.data["recommended_department"] == "心血管内科"

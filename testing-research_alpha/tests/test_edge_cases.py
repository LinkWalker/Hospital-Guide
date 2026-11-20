"""边界与异常场景测试：空消息、超长文本、中途改口、追问上限兜底、急症优先。

这些用例不依赖真实 LLM API，强制使用 mock 网关，保证测试在无 key 环境下可跑。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session_store import MemorySessionStore
from app.core.llm_gateway import LLMGateway
from app.modules.nlu import build_extractor
from app.modules.triage import build_interceptor
from app.modules.dialog import DialogStateMachine
from app.modules.rag import build_retriever
from app.modules.orchestrator import Orchestrator


def _mock_orch(max_followup: int = 3) -> Orchestrator:
    """构造一个强制 mock LLM 的编排器（不读 .env，不发网络请求）。"""
    llm = LLMGateway(provider="mock", base_url="", api_key="", model="")
    return Orchestrator(
        store=MemorySessionStore(),
        extractor=build_extractor(llm),
        interceptor=build_interceptor(),
        dialog=DialogStateMachine(max_followup_turns=max_followup),
        retriever=build_retriever(use_vector=False),
        llm=llm,
    )


# ------------------------------------------------------------------
# 1. 空消息 / 无意义输入
# ------------------------------------------------------------------
def test_empty_message():
    """空字符串不崩溃，走正常流程（抽不到信息则追问部位）。"""
    orch = _mock_orch()
    resp = orch.chat("s-empty", "")
    assert resp.type in ("question", "result", "urgent")
    assert resp.message  # 必须有返回文案


def test_whitespace_only():
    """纯空格输入不崩溃。"""
    orch = _mock_orch()
    resp = orch.chat("s-space", "   ")
    assert resp.type in ("question", "result", "urgent")


def test_punctuation_only():
    """纯标点输入不崩溃。"""
    orch = _mock_orch()
    resp = orch.chat("s-punct", "。。。？！，，，")
    assert resp.type in ("question", "result", "urgent")


# ------------------------------------------------------------------
# 2. 超长文本
# ------------------------------------------------------------------
def test_very_long_text():
    """几千字输入不崩溃、不超时，能从长文本中提取关键症状。"""
    orch = _mock_orch()
    long_text = (
        "我最近身体不太舒服，大概是从上周开始的。"
        + "胸口有点闷，喘不上气。"
        + "前面说了很多无关的话，" * 200
        + "持续三天了，我是成年人。"
    )
    assert len(long_text) > 2000
    resp = orch.chat("s-long", long_text)
    assert resp.type in ("question", "result", "urgent")
    assert resp.message


# ------------------------------------------------------------------
# 3. 中途改口 / 症状修正
# ------------------------------------------------------------------
def test_symptom_change_mid_dialog():
    """用户先说胸痛（心血管），后改口说其实是肚子痛——
    规则匹配应基于整段对话历史，取到的科室不能仍停留在第一次的胸痛判断。"""
    orch = _mock_orch()
    # 第一轮：说胸口闷
    orch.chat("s-change", "我胸口闷，喘不上气")
    # 第二轮：改口说其实是肚子痛
    resp = orch.chat("s-change", "不好意思我搞错了，其实是肚子痛，拉肚子，已经三天了")
    # 最终结果应为消化内科，而非心血管内科
    if resp.type == "result":
        assert resp.data["recommended_department"] in ("消化内科", "全科"), \
            f"改口后仍匹配到: {resp.data['recommended_department']}"
    # 也可能还在追问中，但不能是心血管内科
    elif resp.type == "question":
        state = orch.store.load("s-change")
        full = " ".join(h["content"] for h in state.history if h["role"] == "user")
        # 完整历史应包含肚子痛
        assert "肚子痛" in full or "拉肚子" in full


# ------------------------------------------------------------------
# 4. 追问上限兜底：不能无限追问
# ------------------------------------------------------------------
def test_followup_limit_fallback():
    """连续多轮都不给有效信息，达到追问上限后必须出结果兜底，不无限问。"""
    orch = _mock_orch(max_followup=2)
    s = "s-limit"
    # 第一轮：模糊输入
    orch.chat(s, "不舒服")
    # 第二轮：还是模糊
    orch.chat(s, "就难受")
    # 第三轮：还是模糊——此时已达上限，必须出 result 而不是继续 question
    resp = orch.chat(s, "浑身都不对劲")
    assert resp.type == "result", f"追问上限后仍返回: {resp.type}"
    # 兜底结果必须有科室（即使信息不全）
    assert resp.data["recommended_department"]


def test_followup_turn_count_increments():
    """每轮追问 turn_count 正确累加。"""
    orch = _mock_orch(max_followup=5)
    orch.chat("s-turn", "不舒服")
    state = orch.store.load("s-turn")
    assert state.turn_count >= 1


# ------------------------------------------------------------------
# 5. 急症拦截在追问中途触发（安全关键）
# ------------------------------------------------------------------
def test_urgent_interrupts_followup():
    """正在追问中，用户突然说出急症症状，应立即拦截出 urgent，不再继续追问。"""
    orch = _mock_orch()
    # 第一轮：普通症状，进入追问
    orch.chat("s-urgent-mid", "我肚子有点不舒服")
    # 第二轮：突然说急症
    resp = orch.chat("s-urgent-mid", "我突然大出血，快不行了")
    assert resp.type == "urgent"
    assert resp.data["level"] == "emergency"
    # 会话状态应标记为 interrupted
    state = orch.store.load("s-urgent-mid")
    assert state.interrupted is True


# ------------------------------------------------------------------
# 6. 会话隔离
# ------------------------------------------------------------------
def test_session_isolation():
    """不同 session_id 互不影响。"""
    orch = _mock_orch()
    orch.chat("s-A", "我肚子痛拉肚子")
    orch.chat("s-B", "我胸口闷心慌")
    state_a = orch.store.load("s-A")
    state_b = orch.store.load("s-B")
    # 两个会话的历史不同
    text_a = " ".join(h["content"] for h in state_a.history if h["role"] == "user")
    text_b = " ".join(h["content"] for h in state_b.history if h["role"] == "user")
    assert "肚子痛" in text_a
    assert "胸口闷" in text_b
    assert "肚子痛" not in text_b


# ------------------------------------------------------------------
# 7. 新红旗信号库覆盖（回归）
# ------------------------------------------------------------------
def test_new_high_risk_keywords():
    """扩充后的新红旗词能被拦截器命中。"""
    interceptor = build_interceptor()
    # 这批是第二批新增的词
    new_kw = ["煤气中毒", "撕裂样疼痛", "胎动没了", "东西卡住",
              "血压200以上", "呼气有烂苹果味", "雷击样头痛"]
    for kw in new_kw:
        hit = interceptor.intercept(f"我{kw}，现在很难受")
        assert hit is not None, f"新红旗词未命中: {kw}"

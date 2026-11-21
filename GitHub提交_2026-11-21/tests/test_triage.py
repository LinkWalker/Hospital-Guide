"""急症拦截模块测试：高危词全覆盖 + 未命中路径。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.triage import build_interceptor


def test_emergency_keyword():
    """emergency 级关键词命中紧急。"""
    interceptor = build_interceptor()
    hit = interceptor.intercept("我胸痛伴出冷汗，现在很严重")
    assert hit is not None
    assert hit["level"] == "emergency"
    assert "120" in hit["guide"]


def test_urgent_keyword():
    """urgent 级关键词命中次紧急。"""
    interceptor = build_interceptor()
    hit = interceptor.intercept("我一直高烧不退")
    assert hit is not None
    assert hit["level"] == "urgent"


def test_more_urgent_wins():
    """同时含多级词时以 emergency 优先。"""
    interceptor = build_interceptor()
    hit = interceptor.intercept("呼吸困难还高烧不退")
    assert hit["level"] == "emergency"


def test_normal_text_no_intercept():
    """普通症状不触发拦截。"""
    interceptor = build_interceptor()
    hit = interceptor.intercept("我肚子有点疼，想吃点清淡的")
    assert hit is None


def test_all_emergency_keywords_covered():
    """全部 emergency 高危词都能被命中（全覆盖回归）。"""
    interceptor = build_interceptor()
    from app.knowledge.loader import load_high_risk
    data = load_high_risk()
    for kw in data["level"]["emergency"]["keywords"]:
        assert interceptor.intercept(kw) is not None, f"未命中高危词: {kw}"

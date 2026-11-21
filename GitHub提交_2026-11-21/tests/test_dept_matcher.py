"""科室别名/模糊匹配与 LLM 输出归一化测试。"""
import json

import pytest

from app.core.llm_gateway import LLMGateway
from app.modules.dept_matcher import match_department
from app.modules.rag import build_retriever


@pytest.fixture(scope="module")
def dept_names():
    return build_retriever(use_vector=False).department_names


def test_exact_department(dept_names):
    assert match_department("心血管内科", dept_names) == "心血管内科"


def test_alias_department(dept_names):
    assert match_department("心内科", dept_names) == "心血管内科"
    assert match_department("消化科", dept_names) == "消化内科"
    assert match_department("耳鼻咽喉科", dept_names) == "耳鼻喉科"


def test_alias_with_prefix_and_suffix(dept_names):
    assert match_department("建议挂心内科", dept_names) == "心血管内科"
    assert match_department("耳鼻喉科门诊", dept_names) == "耳鼻喉科"


def test_fuzzy_typo_department(dept_names):
    # “心血内科”是常见漏字写法，应能兜底到标准名
    assert match_department("心血内科", dept_names) == "心血管内科"


def test_compound_alias_not_hijacked_by_short_name(dept_names):
    # “普外科”本身是标准名，不能被“外科 -> 普通外科”的短别名抢走
    assert match_department("普外科", dept_names) == "普外科"
    assert match_department("普外", dept_names) == "普通外科"


def test_unknown_department_rejected(dept_names):
    assert match_department("外星人科", dept_names) is None
    assert match_department("", dept_names) is None
    assert match_department("科", dept_names) is None


def test_llm_validate_normalizes_aliases(dept_names):
    gateway = LLMGateway("openai", base_url="https://example.com/v1", api_key="x", model="test")
    content = json.dumps({
        "recommended_department": "心内科",
        "alternative_departments": ["耳鼻咽喉科", "不存在的科", "心内科"],
        "basis": ["测试依据"],
        "booking_guide": "挂号",
        "navigation": "导航",
        "dialogue_script": "医生您好",
        "reminders": ["带病历"],
        "disclaimer": "仅供参考",
    }, ensure_ascii=False)
    result = gateway._validate_content(content, dept_names)
    assert result.recommended_department == "心血管内科"
    assert result.alternative_departments == ["耳鼻喉科"]


def test_llm_validate_rejects_unknown_main_department(dept_names):
    gateway = LLMGateway("openai", base_url="https://example.com/v1", api_key="x", model="test")
    content = json.dumps({
        "recommended_department": "外星人科",
        "alternative_departments": [],
        "basis": [],
        "booking_guide": "",
        "navigation": "",
        "dialogue_script": "",
        "reminders": [],
        "disclaimer": "仅供参考",
    }, ensure_ascii=False)
    with pytest.raises(ValueError):
        gateway._validate_content(content, dept_names)


def test_llm_fallback_to_mock_on_failure(dept_names, monkeypatch):
    gateway = LLMGateway(
        "openai", base_url="https://example.com/v1", api_key="x", model="test", max_retries=1
    )

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(gateway, "_generate_via_llm", boom)
    from app.schemas.models import SymptomInfo

    result = gateway.generate_triage(
        symptom=SymptomInfo(part="胸部", nature="闷"),
        rule_hit={"recommended_dept": "心血管内科", "alt_depts": ["呼吸内科"], "reason": "测试"},
        evidence=[],
        dept_names=dept_names,
    )
    assert result.recommended_department == "心血管内科"
    assert result.llm_provider == "mock"
    assert result.degraded is True
    assert "RuntimeError" in result.fallback_reason

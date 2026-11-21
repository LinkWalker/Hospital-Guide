"""验证 LLM 网关的科室枚举校验防线（无需真实 API Key）。

本脚本不发起真实网络请求，通过 mock httpx 响应模拟大模型返回，
验证以下安全防线是否生效：
  1. LLM 返回枚举内科室 → 正常通过，生成 TriageResult；
  2. LLM 返回枚举外科室（如"肿瘤科"）→ 必须抛 ValueError，拒绝放行；
  3. LLM 把数组字段返回成字符串 → 自动归一为单元素数组；
  4. LLM 返回带多余文本的 JSON → 自动抽取第一个 {...} 块；
  5. LLM 返回完全非法 JSON → 抛错，不会静默编造科室。

用法：
    python -m scripts.verify_llm_enum
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.llm_gateway import LLMGateway
from app.schemas.models import SymptomInfo
from app.knowledge.loader import load_department_names


# 合法科室白名单（从数据库加载，与线上一致）
DEPT_NAMES = load_department_names()


def _make_gateway() -> LLMGateway:
    """构造一个 openai 模式的网关（base_url/key/model 占位即可，不会真发请求）。"""
    return LLMGateway(
        provider="openai",
        base_url="https://fake.example.com/v1",
        api_key="sk-fake",
        model="fake-model",
        timeout=5.0,
    )


def _mock_response(content: str) -> MagicMock:
    """构造一个 mock 的 httpx.Response。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def _run_llm(gateway: LLMGateway, llm_return: str):
    """用 mock 响应跑一次 generate_triage，返回 (是否通过, 异常或结果)。"""
    symptom = SymptomInfo(part="胸部", nature="痛", duration="3天")
    rule_hit = {"recommended_dept": "心血管内科", "alt_depts": ["急诊科"],
                "reason": "胸痛规则命中"}
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_response(llm_return)
        mock_client_cls.return_value = mock_client
        try:
            result = gateway.generate_triage(
                symptom=symptom, rule_hit=rule_hit, evidence=[],
                dept_names=DEPT_NAMES,
            )
            return True, result
        except Exception as e:
            return False, e


def main():
    print(f"科室白名单共 {len(DEPT_NAMES)} 个：{DEPT_NAMES}\n")
    gw = _make_gateway()

    # 用一个枚举内合法科室构造正常返回
    valid_dept = "心血管内科"
    cases = [
        # (描述, LLM返回内容, 期望通过?, 备注)
        (
            "正常：返回枚举内科室",
            json.dumps({
                "recommended_department": valid_dept,
                "alternative_departments": ["急诊科"],
                "basis": ["胸痛建议心内科排查"],
                "booking_guide": "挂心血管内科",
                "navigation": "门诊楼3层",
                "dialogue_script": "医生我胸口痛三天了",
                "reminders": ["带既往病历"],
            }, ensure_ascii=False),
            True, "应通过校验",
        ),
        (
            "安全防线：返回枚举外科室「肿瘤科」",
            json.dumps({
                "recommended_department": "肿瘤科",
                "alternative_departments": [],
                "basis": ["LLM编造的科室"],
            }, ensure_ascii=False),
            False, "必须抛 ValueError，拒绝放行",
        ),
        (
            "安全防线：返回完全不存在的科室「外星科」",
            json.dumps({
                "recommended_department": "外星科",
                "alternative_departments": [],
            }, ensure_ascii=False),
            False, "必须抛 ValueError",
        ),
        (
            "容错：数组字段返回成字符串（自动归一）",
            json.dumps({
                "recommended_department": valid_dept,
                "alternative_departments": "急诊科",
                "basis": "单一依据",
                "reminders": "注意休息",
            }, ensure_ascii=False),
            True, "字符串应自动包成单元素数组",
        ),
        (
            "容错：JSON 前后带多余解释文字（自动抽取{}块）",
            f"好的，根据您的情况，导诊结果如下：\n"
            + json.dumps({"recommended_department": valid_dept,
                          "basis": ["胸痛"]}, ensure_ascii=False)
            + "\n希望对您有帮助。",
            True, "应抽取第一个 JSON 对象",
        ),
        (
            "安全防线：完全非法文本（无 JSON）",
            "我觉得你应该去看肿瘤科，随便挂个号就行。",
            False, "必须抛错，不能静默编造",
        ),
    ]

    passed = 0
    for desc, llm_ret, expect_pass, note in cases:
        ok, result = _run_llm(gw, llm_ret)
        if ok == expect_pass:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL"
        print(f"[{status}] {desc}")
        print(f"      期望: {'通过' if expect_pass else '拒绝'} | 实际: {'通过' if ok else '拒绝'}")
        if not ok and expect_pass:
            print(f"      异常: {result}")
        if ok and not expect_pass:
            print(f"      !! 安全漏洞：非法科室被放行 -> {result.recommended_department}")
        print(f"      备注: {note}\n")

    print(f"===== 结果: {passed}/{len(cases)} 通过 =====")
    if passed == len(cases):
        print("科室枚举校验防线生效：LLM 无法编造不存在的科室。")
    else:
        print("存在未通过用例，请检查 llm_gateway._validate_content 逻辑。")
        sys.exit(1)


if __name__ == "__main__":
    main()

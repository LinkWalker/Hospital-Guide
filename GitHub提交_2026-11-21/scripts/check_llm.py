"""真实大模型连通性 / 结构化输出自检。

用法（在 medical-triage-backend 目录）：
    python scripts/check_llm.py

它会：
1. 读取 .env / 环境变量，打印当前 provider / model / base_url（不打印 Key）；
2. 用一条固定症状走完整的 generate_triage 流程；
3. 校验返回科室是否在白名单内、是否发生降级；
4. 打印结果与耗时，失败时给出排查提示。

退出码：0 = 成功（mock 模式也会提示如何切换）；1 = 配置缺失或调用失败/降级。
"""
from __future__ import annotations

import os
import sys
import time

# 让脚本可以从项目根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.core.llm_gateway import build_llm_gateway
from app.modules.rag import build_retriever
from app.schemas.models import SymptomInfo


def main() -> int:
    settings = get_settings()
    provider = (settings.llm_provider or "mock").lower()
    print("=" * 62)
    print("智能导诊 · 大模型自检")
    print("=" * 62)
    print(f"provider : {provider}")
    print(f"model    : {settings.llm_model or '(未配置)'}")
    print(f"base_url : {settings.llm_base_url or '(未配置)'}")
    print(f"api_key  : {'已配置（不显示）' if settings.llm_api_key else '(未配置)'}")
    print(f"timeout  : {settings.llm_timeout}s / retries: {settings.llm_max_retries}")
    print("-" * 62)

    if provider == "mock":
        print("当前是 mock 模式：不需要 Key，适合离线演示。")
        print("要接真实模型，请复制 .env.example 为 .env，然后：")
        print("  1) 把 LLM_PROVIDER 改成 openai")
        print("  2) 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
        print("  3) 重新运行本脚本")
        print("\n各平台配置见 docs/接大模型指南.md")
        return 0

    missing = [
        name for name, value in (
            ("LLM_BASE_URL", settings.llm_base_url),
            ("LLM_API_KEY", settings.llm_api_key),
            ("LLM_MODEL", settings.llm_model),
        ) if not value
    ]
    if missing:
        print("配置不完整，缺少：" + "、".join(missing))
        print("请检查 .env 是否在项目根目录，以及变量名是否拼写正确。")
        return 1

    retriever = build_retriever(use_vector=False)
    symptom = SymptomInfo(part="胸部", nature="闷", duration="三天", population="成年男性")
    raw_text = "我胸口闷，已经三天了，我是成年男性"
    retrieved = retriever.retrieve(symptom, raw_text=raw_text)
    rule_hit = retrieved["rule_hit"] or {}
    evidence = retrieved["evidence"]

    print(f"样例输入 : {raw_text}")
    print(f"规则命中 : {rule_hit.get('recommended_dept', '(无)')}")
    print("正在调用真实模型……")

    gateway = build_llm_gateway(settings)
    started = time.perf_counter()
    try:
        result = gateway.generate_triage(
            symptom=symptom,
            rule_hit=rule_hit,
            evidence=evidence,
            dept_names=retriever.department_names,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        print(f"调用异常（{elapsed:.2f}s）：{type(exc).__name__}: {exc}")
        print("排查：检查 Key、Base URL、模型名、网络/代理、账户余额与限流。")
        return 1
    elapsed = time.perf_counter() - started

    print("-" * 62)
    print(f"耗时       : {elapsed:.2f}s")
    print(f"生成来源   : {result.llm_provider} / {result.llm_model or '(mock)'}")
    print(f"是否降级   : {result.degraded}" + (f"（{result.fallback_reason}）" if result.fallback_reason else ""))
    print(f"推荐科室   : {result.recommended_department}")
    print(f"备选科室   : {'、'.join(result.alternative_departments) or '(无)'}")
    print(f"判断依据   : {' | '.join(result.basis[:2]) or '(无)'}")
    print(f"就诊话术   : {result.dialogue_script[:120]}")
    print(f"免责声明   : {result.disclaimer}")
    print("=" * 62)

    if result.degraded:
        print("结果：真实模型调用失败，已自动降级到本地规则模板。")
        print("请按上面的 fallback_reason 排查；演示仍可继续，但答辩时应如实说明。")
        return 1
    print("结果：成功。真实模型返回已通过科室白名单 / Schema 校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

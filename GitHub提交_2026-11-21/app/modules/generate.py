"""结构化生成模块（预留）。

当前结构化生成逻辑集中在 app/core/llm_gateway.py 的 generate_triage 中，
含 Schema 校验与科室枚举约束。若后续生成逻辑变复杂，可把"LLM 调用"与
"生成策略/重试"拆到本文件，网关只负责底层大模型对话。

预留接口：
    def generate(symptom, rule_hit, evidence, dept_names) -> TriageResult
"""

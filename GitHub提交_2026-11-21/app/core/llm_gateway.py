"""LLM 网关：统一大模型调用抽象。

- openai 模式：走 OpenAI 兼容协议（豆包 / DeepSeek / 通义 / 智谱等），需要 base_url + api_key + model。
- mock 模式：本地规则模板生成结构化结果，无需任何 key，保证无网络也能演示全流程。

Mock 模式不是"骗人"，而是把 LLM 输出的稳定性问题在工程层面隔离：
接上真实大模型后，只需改 .env 配置，业务代码不动。
"""
import json
import logging
from typing import Optional

import httpx

from ..schemas.models import TriageResult, SymptomInfo
from ..modules.dept_matcher import match_department

logger = logging.getLogger(__name__)


class LLMGateway:
    """大模型网关。根据配置选择真实调用或本地降级。"""

    def __init__(
        self,
        provider: str,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 30.0,
        max_retries: int = 1,
    ):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))

    @property
    def is_mock(self) -> bool:
        return self.provider != "openai"

    def _openai_chat(self, messages: list) -> Optional[str]:
        """调用 OpenAI 兼容的 /chat/completions 接口。"""
        if not (self.base_url and self.api_key and self.model):
            raise RuntimeError("LLM 网关配置不完整：需要 llm_base_url / llm_api_key / llm_model")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # 生成结构化导诊结果
    # ------------------------------------------------------------------
    def generate_triage(
        self,
        symptom: SymptomInfo,
        rule_hit: dict,
        evidence: list,
        dept_names: list,
    ) -> TriageResult:
        """根据抽取的症状 + 规则命中 + 检索证据，生成结构化导诊结果。

        真实模型可能超时、返回非 JSON、返回枚举外科室；任何失败都不能让用户看到 500：
        - 最多重试 max_retries 次；
        - 最终失败自动降级为本地规则模板，并在结果里标记 degraded=True。
        """
        if self.is_mock:
            result = self._mock_generate(symptom, rule_hit, evidence)
            result.llm_provider = "mock"
            result.llm_model = ""
            return result

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                content = self._generate_via_llm(symptom, rule_hit, evidence, dept_names)
                result = self._validate_content(content, dept_names)
                result.llm_provider = "openai"
                result.llm_model = self.model
                return result
            except Exception as exc:  # noqa: BLE001 - 这里必须兜住所有模型侧异常
                last_error = exc
                logger.warning(
                    "LLM 生成失败（%s/%s）：%s: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    type(exc).__name__,
                    exc,
                )

        # 最终降级：用户仍然拿到可用的分诊结果，前端会标注“本地稳定模式”
        result = self._mock_generate(symptom, rule_hit, evidence)
        result.llm_provider = "mock"
        result.llm_model = ""
        result.degraded = True
        result.fallback_reason = self._safe_error(last_error)
        return result

    # ---------------- 真实大模型路径 ----------------
    def _generate_via_llm(self, symptom, rule_hit, evidence, dept_names) -> str:
        dept_list = "、".join(dept_names)
        evidence_txt = "\n".join(f"- {e.text}" for e in evidence) or "（无检索证据）"
        sys_prompt = (
            "你是一个医院智能导诊助手的结构化输出模块。\n"
            "硬性规则：\n"
            "1. 只允许从给定科室枚举中选取科室，禁止编造不存在的科室；\n"
            "2. 判断依据只能来自给定检索资料，禁止自行诊断或添加医学结论；\n"
            "3. 不提供治疗建议、不承诺疗效；\n"
            "4. 输出必须是一个 JSON 对象，字段及类型：recommended_department（字符串）、"
            "alternative_departments（字符串数组）、basis（字符串数组）、booking_guide（字符串）、"
            "navigation（字符串）、dialogue_script（字符串）、reminders（字符串数组）、disclaimer（字符串）。\n"
            "5. dialogue_script 是【就诊话术】：以患者第一人称、挂号后向医生描述症状的口语化陈述，"
            "包含起病时间、部位、症状性质与伴随症状、人群特征（如老人/儿童/孕妇需说明）；"
            "不要出现挂号流程、不要出现科室选择建议、不要编造用户未提供的病史。\n"
            f"可选科室枚举：{dept_list}\n"
            f"检索资料（仅此为依据）：\n{evidence_txt}"
        )
        user_prompt = (
            "请根据以下结构化症状信息输出导诊 JSON：\n"
            f"{json.dumps(symptom.model_dump(), ensure_ascii=False)}\n"
            f"规则库命中（可作为主推科室参考）：{json.dumps(rule_hit, ensure_ascii=False)}\n"
        )
        content = self._openai_chat([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ])
        return content

    def _validate_content(self, content: str, dept_names: list) -> TriageResult:
        """解析 LLM 返回并做代码侧校验。

        关键变化：科室不再要求“字符串完全相等”，而是先经过别名/模糊归一化。
        这样模型返回“心内科”“耳鼻咽喉科”时不会被误判为编造；
        但无法可靠匹配的科室仍然拒绝，避免把用户带到不存在的科室。
        """
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # 尝试抽取第一个 { ... } 块
            text = str(content or "")
            start, end = text.find("{"), text.rfind("}")
            data = json.loads(text[start:end + 1]) if start != -1 and end != -1 else {}
        if not isinstance(data, dict):
            raise ValueError("LLM 返回的不是 JSON 对象")

        raw_main = data.get("recommended_department")
        main = match_department(raw_main, dept_names)
        if not main:
            raise ValueError(f"LLM 返回了无法归一的科室：{raw_main!r}")
        data["recommended_department"] = main

        # 备选科室：能归一则保留，归一则丢弃（不因为一个备选拖垮整个结果）
        raw_alts = data.get("alternative_departments") or []
        if isinstance(raw_alts, str):
            raw_alts = [raw_alts]
        alts = []
        for raw_alt in raw_alts:
            alt = match_department(raw_alt, dept_names)
            if alt and alt != main and alt not in alts:
                alts.append(alt)
        data["alternative_departments"] = alts[:3]

        # 数组字段容错：模型偶尔把数组字段返回成字符串，自动归一为单元素数组
        for arr_field in ("basis", "reminders"):
            v = data.get(arr_field)
            if isinstance(v, str):
                data[arr_field] = [v] if v.strip() else []
        return TriageResult.model_validate(data)

    @staticmethod
    def _safe_error(exc: Optional[Exception]) -> str:
        """降级原因脱敏：只保留异常类型/HTTP 状态码，不把 Key、URL、模型原文写进响应。"""
        if exc is None:
            return "unknown"
        name = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            return f"{name} {exc.response.status_code}"
        if isinstance(exc, (ValueError, KeyError, TypeError, json.JSONDecodeError)):
            return f"{name}: {str(exc)[:80]}"
        return name

    # ---------------- 本地降级路径（无需 key） ----------------
    def _mock_generate(self, symptom: SymptomInfo, rule_hit: dict, evidence: list) -> TriageResult:
        dept = rule_hit.get("recommended_dept") or "综合门诊"
        alts = rule_hit.get("alt_depts") or []
        reason = rule_hit.get("reason") or "根据您描述的症状初步判断"
        part_txt = symptom.part or "相关"
        basis = [reason]
        basis += [e.text for e in evidence[:2]]
        booking_guide = "可前往医院官方挂号平台或到院挂号窗口，选择「" + dept + "」进行挂号。"
        navigation = "挂号完成后，请根据医院院内导视前往" + dept + "所在楼层，如有疑问可询问导诊台。"
        dialogue_script = self._compose_doctor_script(symptom)
        reminders = ["建议携带既往病历和检查报告", "如症状加重请及时就医"]
        if symptom.population:
            reminders.append(f"注意：{symptom.population}，请同时向医生说明具体情况")
        return TriageResult(
            recommended_department=dept,
            alternative_departments=alts,
            basis=basis,
            booking_guide=booking_guide,
            navigation=navigation,
            dialogue_script=dialogue_script,
            reminders=reminders,
        )

    @staticmethod
    def _compose_doctor_script(s: SymptomInfo) -> str:
        """生成【就诊话术】：患者挂号后向医生描述症状的口语化陈述。

        要点：第一人称、包含起病时间/部位/性质/伴随症状/人群特征，
        不提及挂号流程与科室选择。
        """
        place = s.part or "身体"
        nature = s.nature or ""
        # 口语自然化：单字性质补成完整表述
        _map = {"痛": "疼痛", "闷": "发闷", "胀": "胀痛", "酸": "酸痛", "麻": "发麻", "痒": "瘙痒"}
        nature = _map.get(nature, nature)
        seg = ["医生您好，"]
        if s.duration:
            seg.append(f"我最近{s.duration}出现{place}{nature or '不适'}")
        else:
            seg.append(f"我最近{place}{nature or '不适'}")
        if s.accompanying:
            seg.append("，还伴有" + "、".join(s.accompanying))
        seg.append("。")
        if s.population and s.population not in ("成人", "成年人"):
            seg.append(f"我是{s.population}。")
        seg.append("想请您看看需要做什么检查、日常要注意什么，谢谢。")
        return "".join(seg)

    @staticmethod
    def _describe(s: SymptomInfo) -> str:
        parts = [s.nature] if s.nature else []
        parts += s.accompanying
        return "、".join(parts) if parts else "有些不适"


def build_llm_gateway(settings) -> LLMGateway:
    from ..core.config import get_settings
    if settings is None:
        settings = get_settings()
    return LLMGateway(
        provider=settings.llm_provider,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout,
        max_retries=getattr(settings, "llm_max_retries", 1),
    )

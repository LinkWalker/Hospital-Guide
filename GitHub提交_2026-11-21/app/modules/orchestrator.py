"""Agent 编排层：把五个模块串成完整流程。

流程：症状输入 → 信息抽取 → 急症拦截 → 追问判断 → 知识检索 → 结构化生成 → 返回。
编排层持有会话状态，决定每一步该走哪个模块。
"""
import re

from ..schemas.models import ChatResponse, SessionState, SymptomInfo
from ..core.llm_gateway import LLMGateway
from ..db.session_store import SessionStore
from .nlu import InfoExtractor
from .triage import TriageInterceptor
from .dialog import DialogStateMachine
from .rag import KnowledgeRetriever


class Orchestrator:
    """导诊对话编排器。"""

    def __init__(
        self,
        store: SessionStore,
        extractor: InfoExtractor,
        interceptor: TriageInterceptor,
        dialog: DialogStateMachine,
        retriever: KnowledgeRetriever,
        llm: LLMGateway,
        history=None,
    ):
        self.store = store
        self.extractor = extractor
        self.interceptor = interceptor
        self.dialog = dialog
        self.retriever = retriever
        self.llm = llm
        # 历史记录是可选能力：测试/隐私关闭时传 None，不影响主流程
        self.history = history

    # ------------------------------------------------------------------
    def chat(self, session_id: str, message: str) -> ChatResponse:
        # 1) 取会话状态（新会话则创建）
        state = self.store.load(session_id)
        if state is None:
            state = SessionState(session_id=session_id)
        state.history.append({"role": "user", "content": message})

        # 2) 急症拦截：纯代码，最先判断（安全关键）
        hit = self.interceptor.intercept(message)
        if hit:
            state.interrupted = True
            state.status = "interrupted"
            self._append_assistant(state, hit["guide"])
            self.store.save(state)
            data = {"level": hit["level"], "label": hit["label"], "keyword": hit["keyword"]}
            self._save_history(state, "urgent", data)
            return ChatResponse(
                session_id=session_id,
                type="urgent",
                message=hit["guide"],
                data=data,
            )

        # 明确改口：保留审计历史，重新收集当前主诉，避免旧症状污染推荐。
        correction = re.search(r"说错了|搞错|弄错|改口|更正|不是.{0,20}(?:而是|是)", message)
        if correction:
            state.collected = SymptomInfo()
            state.asked = []
            state.last_question = None
            state.turn_count = 0
            state.history[-1]["correction"] = True

        # 3) 信息抽取
        current_message = message
        if correction:
            current_message = re.split(r"而是|说错了[，,：:]?|改口[，,：:]?|更正[，,：:]?", message)[-1]
            current_message = re.sub(r"^不是.{0,20}?是", "", current_message)
            state.history[-1]["current_complaint"] = current_message
        symptom = self.extractor.extract(current_message)

        # 4) 合并进会话，更新追问轮数
        state = self.dialog.merge_message(state, symptom)

        # 5) 判断是否还需要追问
        if self.dialog.should_ask(state):
            question = self.dialog.next_question(state)
            state.last_question = question
            state.status = "collecting"
            self._append_assistant(state, question)
            self.store.save(state)
            data = {"collected": state.collected.model_dump()}
            self._save_history(state, "question", data)
            return ChatResponse(
                session_id=session_id,
                type="question",
                message=question,
                data=data,
            )

        # 6) 信息足够（或达追问上限）：检索 + 生成
        state.status = "ready"
        # 规则匹配要基于整段对话累计信息，而不是最后一轮文本（"胸口闷"可能出现在第一轮）
        users = [h for h in state.history if h.get("role") == "user"]
        start = max((i for i, h in enumerate(users) if h.get("correction")), default=0)
        full_text = " ".join(h.get("current_complaint", h["content"]) for h in users[start:])
        retrieved = self.retriever.retrieve(state.collected, raw_text=full_text)
        rule_hit = retrieved["rule_hit"]
        evidence = retrieved["evidence"]

        result = self.llm.generate_triage(
            symptom=state.collected,
            rule_hit=rule_hit or {},
            evidence=evidence,
            dept_names=self.retriever.department_names,
        )
        reply = self._compose_reply(result)
        self._append_assistant(state, reply)
        self.store.save(state)
        data = {
            "recommended_department": result.recommended_department,
            "alternative_departments": result.alternative_departments,
            "basis": result.basis,
            "booking_guide": result.booking_guide,
            "navigation": result.navigation,
            "dialogue_script": result.dialogue_script,
            "reminders": result.reminders,
            "disclaimer": result.disclaimer,
            "evidence": [e.text for e in evidence],
            "llm_provider": result.llm_provider,
            "llm_model": result.llm_model,
            "degraded": result.degraded,
            "fallback_reason": result.fallback_reason,
        }
        self._save_history(state, "result", data)
        return ChatResponse(
            session_id=session_id,
            type="result",
            message=reply,
            data=data,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _compose_reply(result) -> str:
        lines = [
            f"根据您描述的症状，建议挂【{result.recommended_department}】。",
            "",
            "判断依据：",
        ]
        lines += [f"· {b}" for b in result.basis]
        if result.alternative_departments:
            lines.append("备选科室：" + "、".join(result.alternative_departments))
        lines += [
            "",
            "挂号指引：" + result.booking_guide,
            "院内导航：" + result.navigation,
            "",
            "就诊沟通话术：" + result.dialogue_script,
            "就医提醒：" + "；".join(result.reminders),
            "",
            result.disclaimer,
        ]
        return "\n".join(lines)

    @staticmethod
    def _append_assistant(state: SessionState, content: str) -> None:
        state.history.append({"role": "assistant", "content": content})

    def _save_history(self, state: SessionState, response_type: str, data: dict) -> None:
        """把当前会话快照写入历史；历史失败不能影响正常对话。"""
        if self.history is None:
            return
        try:
            department = None
            urgent_level = None
            if response_type == "result":
                department = data.get("recommended_department")
            elif response_type == "urgent":
                urgent_level = data.get("level")
            self.history.save(
                session_id=state.session_id,
                messages=state.history,
                status=state.status,
                state=state.model_dump(),
                result=data,
                department=department,
                urgent_level=urgent_level,
            )
        except Exception:  # noqa: BLE001 - 历史是旁路能力，不能拖垮主流程
            import logging
            logging.getLogger(__name__).exception("写入会话历史失败：%s", state.session_id)

"""依赖组装：单例构建各模块并注入编排器。"""
from ..core.config import get_settings
from ..core.llm_gateway import build_llm_gateway
from ..db.session_store import build_session_store
from ..db.history_store import build_history_store
from ..modules.nlu import build_extractor
from ..modules.triage import build_interceptor
from ..modules.dialog import DialogStateMachine
from ..modules.rag import build_retriever
from ..modules.orchestrator import Orchestrator

_orchestrator = None
_history_store = None
_history_loaded = False


def get_history_store():
    """懒加载历史存储；history_enabled=false 时返回 None。"""
    global _history_store, _history_loaded
    if not _history_loaded:
        settings = get_settings()
        _history_store = build_history_store(settings)
        _history_loaded = True
    return _history_store


def get_orchestrator() -> Orchestrator:
    """懒加载单例编排器。"""
    global _orchestrator
    if _orchestrator is None:
        settings = get_settings()
        llm = build_llm_gateway(settings)
        store = build_session_store(settings)
        extractor = build_extractor(llm)
        interceptor = build_interceptor()
        dialog = DialogStateMachine(max_followup_turns=settings.max_followup_turns)
        retriever = build_retriever(use_vector=(settings.retriever == "vector"))
        _orchestrator = Orchestrator(
            store=store,
            extractor=extractor,
            interceptor=interceptor,
            dialog=dialog,
            retriever=retriever,
            llm=llm,
            history=get_history_store(),
        )
    return _orchestrator

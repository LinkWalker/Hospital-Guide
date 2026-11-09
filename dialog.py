"""多轮追问状态机：决定"该追问哪个缺失字段"还是"可以出结果"。

设计要点：
- 用已收集字段集合判断缺失项；
- 每轮最多追问 1 个问题，不重复已问；
- 追问轮数达到上限后，用已收集信息兜底出结果（带条件 + 就医提醒），不无限问下去。
"""
from ..schemas.models import SymptomInfo, SessionState

# 字段收集优先级（部位 → 性质 → 时长 → 人群特征）
# 伴随症状不作为强制追问项：从用户文本主动抽取即可，避免反复追问影响体验
_FIELD_PRIORITY = [
    ("part", "您具体是哪个部位不舒服呢？"),
    ("nature", "这种不适是什么感觉呢？比如是痛、闷、胀还是其他？"),
    ("duration", "这个症状大概持续多长时间了？"),
    ("population", "请问就诊对象是成人、老人、儿童还是孕妇呢？"),
]


class DialogStateMachine:
    """多轮追问状态机。不持有存储，只做状态推进决策（由编排层调用）。"""

    def __init__(self, max_followup_turns: int = 3):
        self.max_followup_turns = max_followup_turns

    def next_question(self, state: SessionState) -> str | None:
        """返回下一个要追问的问题；None 表示信息已足够、可以出结果。"""
        if state.interrupted:
            return None
        # 按优先级找缺失字段（不在已问列表里）
        for field, question in _FIELD_PRIORITY:
            if getattr(state.collected, field) is None and question not in state.asked:
                return question
        return None

    def should_ask(self, state: SessionState) -> bool:
        """判断当前是否还需要追问。"""
        q = self.next_question(state)
        if q is None:
            return False
        # 已到追问上限：不再追问，改为兜底出结果
        return state.turn_count < self.max_followup_turns

    def merge_message(self, state: SessionState, extractor_result: SymptomInfo) -> SessionState:
        """把本轮抽取到的信息合并进会话状态（去重：已收集的不覆盖为 None）。"""
        collected = state.collected
        # 仅填充仍为 None 的字段
        for field in ("part", "nature", "duration", "population"):
            if getattr(collected, field) is None and getattr(extractor_result, field):
                setattr(collected, field, getattr(extractor_result, field))
        collected.accompanying = list(dict.fromkeys(collected.accompanying + extractor_result.accompanying))
        # 记录本轮问题
        if state.last_question and state.last_question not in state.asked:
            state.asked.append(state.last_question)
        state.turn_count += 1
        return state

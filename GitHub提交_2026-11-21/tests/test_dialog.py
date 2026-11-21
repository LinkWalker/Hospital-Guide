"""多轮追问状态机测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas.models import SessionState, SymptomInfo
from app.modules.dialog import DialogStateMachine


def test_first_question_is_part():
    dialog = DialogStateMachine()
    state = SessionState(session_id="s1")
    assert dialog.should_ask(state)
    assert "部位" in dialog.next_question(state)


def test_merge_and_progress():
    dialog = DialogStateMachine()
    state = SessionState(session_id="s1")
    # 第一轮：只给部位
    merged = dialog.merge_message(state, SymptomInfo(part="胸部"))
    # 第二问应该是性质
    q = dialog.next_question(merged)
    assert "感觉" in q


def test_no_repeat_and_finish():
    dialog = DialogStateMachine()
    state = SessionState(session_id="s1")
    state.collected = SymptomInfo(part="胸部", nature="痛", duration="三天", population="成人")
    # 已收集完整关键字段
    assert dialog.next_question(state) is None
    assert dialog.should_ask(state) is False


def test_followup_turn_limit():
    """追问达到上限后不再追问，走兜底出结果。"""
    dialog = DialogStateMachine(max_followup_turns=2)
    state = SessionState(session_id="s1")
    state.collected = SymptomInfo(part="胸部")  # 只缺 nature
    # 前两轮都在上限内应追问
    assert dialog.should_ask(state) is True
    state.turn_count = 2
    # 已到上限，不再追问
    assert dialog.should_ask(state) is False

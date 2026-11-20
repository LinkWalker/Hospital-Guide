"""任务四：通过HTTP契约验证异常输入和多轮边界，独立内存会话及mock模型。"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api import routes
from app.core.llm_gateway import LLMGateway
from app.db.session_store import MemorySessionStore
from app.modules.orchestrator import Orchestrator
from app.modules.nlu import build_extractor
from app.modules.triage import build_interceptor
from app.modules.dialog import DialogStateMachine
from app.modules.rag import build_retriever

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr('app.db.session_store._MEM', {})
    monkeypatch.setattr('app.db.session_store._MEM', {})
    monkeypatch.setattr('app.db.session_store._MEM', {})
    llm = LLMGateway('mock')
    orch = Orchestrator(MemorySessionStore(), build_extractor(llm), build_interceptor(),
                        DialogStateMachine(3), build_retriever(), llm)
    monkeypatch.setattr(routes, 'get_orchestrator', lambda: orch)
    with TestClient(app) as c:
        yield c

def send(c, text, sid='boundary'):
    return c.post('/api/v1/chat', json={'session_id': sid, 'message': text})

@pytest.mark.parametrize('text', ['', '   ', '\n\t', '字'*4001])
def test_invalid_input_no_state(client, text):
    assert send(client, text).status_code == 422
    assert client.get('/api/v1/session/boundary').status_code == 404

@pytest.mark.parametrize('size', [1,4000])
def test_length_boundary(client, size):
    r = send(client, '啊'*size)
    assert r.status_code == 200
    assert r.json()['type'] == 'question'

@pytest.mark.parametrize('payload', [{}, {'session_id':'a'}, {'session_id':'','message':'疼'},
                                      {'session_id':'a','message':None}])
def test_invalid_contract(client, payload):
    assert client.post('/api/v1/chat', json=payload).status_code == 422

def test_followup_limit_fallback(client):
    replies = [send(client,'不知道').json() for _ in range(3)]
    assert [r['type'] for r in replies] == ['question','question','result']
    assert replies[0]['message'] != replies[1]['message']
    assert replies[-1]['data']['recommended_department'] == '综合门诊'
    assert replies[-1]['data']['disclaimer']
    state = client.get('/api/v1/session/boundary').json()
    assert state['turn_count'] == 3 and state['status'] == 'ready'

def test_explicit_correction(client):
    send(client,'我胸口闷')
    r = send(client,'说错了，不是胸口闷，而是肚子疼，持续三天，我是成年人').json()
    if r['type'] == 'question':
        r = send(client,'疼痛，三天，成年人').json()
    if r['type'] == 'question':
        r = send(client,'成年人').json()
    assert r['type'] == 'result'
    assert r['data']['recommended_department'] == '消化内科'
    assert client.get('/api/v1/session/boundary').json()['collected']['part'] == '腹部'

def test_urgent_during_followup(client):
    send(client,'不舒服')
    r = send(client,'胸痛伴出冷汗').json()
    assert r['type'] == 'urgent' and '120' in r['message']
    assert client.get('/api/v1/session/boundary').json()['interrupted'] is True

def test_sessions_isolated(client):
    send(client,'肚子疼','a')
    assert client.get('/api/v1/session/b').status_code == 404
    send(client,'头痛','b')
    assert client.get('/api/v1/session/a').json()['collected']['part'] == '腹部'


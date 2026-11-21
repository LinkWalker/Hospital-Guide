"""Pydantic 数据模型：前后端契约 + 模块间传递的结构化数据。"""
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------
# 会话状态
# ---------------------------------------------------------------
class SymptomInfo(BaseModel):
    """信息抽取模块的输出：从口语中提取的结构化症状信息。"""
    part: Optional[str] = None            # 部位：胸部 / 腹部 / 头部 / 咽喉 / 关节 / 皮肤 ...
    nature: Optional[str] = None          # 性质：闷 / 痛 / 胀 / 烧灼 / 痒 / 麻 / 头晕 ...
    duration: Optional[str] = None        # 时长：几小时 / 几天 / 一周 / 一个月 ...
    accompanying: List[str] = Field(default_factory=list)  # 伴随症状
    population: Optional[str] = None      # 人群特征：成人 / 老人 / 儿童 / 孕妇 ...


class SessionState(BaseModel):
    """一次对话会话的持久化状态。"""
    session_id: str
    collected: SymptomInfo = Field(default_factory=SymptomInfo)  # 已收集字段
    turn_count: int = 0                    # 已发生的追问轮数
    interrupted: bool = False              # 是否命中急症拦截
    last_question: Optional[str] = None    # 上一轮追问的问题
    asked: List[str] = Field(default_factory=list)  # 已经问过的问题（去重用）
    history: List[Dict] = Field(default_factory=list)  # 对话历史
    status: str = "collecting"             # collecting | interrupted | ready


# ---------------------------------------------------------------
# 对话请求 / 响应
# ---------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str = Field(description="会话 ID，前端生成或首次返回")
    message: str = Field(min_length=1, max_length=4000, description="用户输入，最多4000字符")

    @field_validator("session_id", "message")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空或仅含空白")
        return value


class ChatResponse(BaseModel):
    session_id: str
    type: str = Field(description="question | result | urgent")
    message: str = Field(description="返回给用户的主文案")
    data: Optional[Dict] = None            # 结构化数据（result 时填充科室等）


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    llm_provider: str
    llm_model: str = ""
    llm_ready: bool = False
    session_store: str
    retriever: str
    history_enabled: bool = True


class SessionResponse(BaseModel):
    session_id: str
    status: str
    collected: SymptomInfo
    turn_count: int
    interrupted: bool
    history: List[Dict]


# ---------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------
class HistoryItem(BaseModel):
    """历史列表中的一条会话摘要。"""
    id: str
    title: str
    status: str = "collecting"          # collecting | ready | interrupted
    department: Optional[str] = None
    urgent_level: Optional[str] = None
    message_count: int = 0
    created_at: str
    updated_at: str


class HistoryListResponse(BaseModel):
    items: List[HistoryItem]
    total: int


class HistoryDetail(HistoryItem):
    """历史详情：前端回看用，不暴露内部 SessionState。"""
    messages: List[Dict] = Field(default_factory=list)
    result: Optional[Dict] = None


class HistoryRestoreResponse(BaseModel):
    """恢复历史会话后返回的会话信息。"""
    session_id: str
    status: str
    messages: List[Dict] = Field(default_factory=list)


# ---------------------------------------------------------------
# 医院推荐
# ---------------------------------------------------------------
class HospitalItem(BaseModel):
    name: str
    level: str = ""
    address: str = ""
    latitude: float
    longitude: float
    distance_km: Optional[float] = None
    registration_channel: str = ""
    registration_url: str = ""
    map_url: str = ""
    source: str = "local"


class HospitalListResponse(BaseModel):
    items: List[HospitalItem]
    total: int


class NearbyHospitalsResponse(BaseModel):
    origin: Dict[str, float]
    items: List[HospitalItem]
    source: str = "local"
    message: str = ""


class LocationResolveResponse(BaseModel):
    """用户输入具体地点后解析出的坐标。"""
    label: str
    address: str = ""
    latitude: float
    longitude: float
    source: str = "amap"


class SavedLocationRequest(BaseModel):
    """保存上次使用的医院推荐位置。"""
    label: str = Field(min_length=1, max_length=80)
    address: str = Field(default="", max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SavedLocationResponse(SavedLocationRequest):
    updated_at: str = ""


# ---------------------------------------------------------------
# 结构化生成结果（LLM 输出，受 Schema 约束）
# ---------------------------------------------------------------
class TriageResult(BaseModel):
    """最终导诊结果。科室名必须来自规则库枚举，防 LLM 编造。"""
    recommended_department: str = Field(description="主推科室")
    alternative_departments: List[str] = Field(default_factory=list)
    basis: List[str] = Field(default_factory=list)          # 判断依据
    booking_guide: str = ""                                 # 挂号指引
    navigation: str = ""                                    # 院内导航
    dialogue_script: str = ""                               # 沟通话术
    reminders: List[str] = Field(default_factory=list)      # 就医提醒
    disclaimer: str = "仅供参考，不替代医生诊断"

    # 生成来源元数据：让前端/答辩能说清楚“这次是真实模型还是降级”
    llm_provider: str = "mock"                              # mock | openai
    llm_model: str = ""                                     # 实际模型名
    degraded: bool = False                                  # 真实模型失败后是否降级
    fallback_reason: str = ""                               # 降级原因（脱敏后的异常类型/短信息）


# ---------------------------------------------------------------
# 检索结果
# ---------------------------------------------------------------
class RetrievedDoc(BaseModel):
    """知识检索返回的一条证据。"""
    text: str
    source: str = ""
    score: float = 0.0

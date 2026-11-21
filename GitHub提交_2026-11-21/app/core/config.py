"""应用配置：从环境变量 / .env 读取，均可覆盖。"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_name: str = "智能导诊与挂号导航助手"
    debug: bool = False

    # LLM 网关：llm_provider = "openai"（走真实大模型）| "mock"（本地规则模板，无需 key）
    llm_provider: str = "mock"
    llm_base_url: str = ""          # 例如豆包: https://ark.cn-beijing.volces.com/api/v3
    llm_api_key: str = ""           # 例如豆包: sk-xxxx / DeepSeek: sk-xxxx
    llm_model: str = ""             # 例如豆包: doubao-seed-1-6 / DeepSeek: deepseek-chat
    llm_timeout: float = 30.0

    # 会话存储：session_store = "memory" | "redis"
    session_store: str = "memory"
    redis_url: str = "redis://localhost:6379/0"

    # 知识检索：retriever = "rule"（仅规则库）| "vector"（规则库 + 内置向量降级）
    retriever: str = "vector"

    # 多轮追问上限
    max_followup_turns: int = 3

    # 会话历史：默认开启；可关闭或调整保留天数/条数
    history_enabled: bool = True
    history_retention_days: int = 30
    history_max_items: int = 200

    # 真实大模型失败时的重试次数（不含首次），最终失败自动降级 mock
    llm_max_retries: int = 1

    # 高德 Web 服务 Key（可选）：配置后 /hospitals/nearby 使用真实 POI 周边搜索；
    # 不配置则使用本地医院表 + Haversine 距离（离线可用）。
    amap_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings. Secrets never belong in source control."""

    app_name: str = "Liko API"
    app_version: str = "0.1.0"
    environment: str = "development"
    allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"

    fastgpt_base_url: str | None = None
    fastgpt_api_key: str | None = Field(default=None, repr=False)
    fastgpt_app_id: str | None = None
    fastgpt_timeout_seconds: float = Field(default=60, ge=5, le=180)
    database_url: str | None = Field(default=None, repr=False)
    session_secret: str | None = Field(default=None, repr=False)
    session_days: int = Field(default=30, ge=1, le=90)
    guest_daily_limit: int = Field(default=10, ge=1, le=1000)
    user_daily_limit: int = Field(default=50, ge=1, le=10000)

    memory_enabled: bool = False
    mem0_llm_provider: str = "openai"
    mem0_llm_model: str = "deepseek-v4-flash"
    mem0_llm_base_url: str = "https://api.deepseek.com/v1"
    mem0_llm_api_key: str | None = Field(default=None, repr=False)
    mem0_embedder_provider: str = "openai"
    mem0_embedder_model: str = "BAAI/bge-m3"
    mem0_embedder_base_url: str = "https://api.siliconflow.cn/v1"
    mem0_embedder_api_key: str | None = Field(default=None, repr=False)
    mem0_embedding_dims: int = Field(default=1024, ge=1, le=8192)
    mem0_postgres_url: str | None = Field(default=None, repr=False)
    mem0_collection_name: str = "liko_memories"
    mem0_history_db_path: str = "data/mem0-history.db"
    mem0_top_k: int = Field(default=5, ge=1, le=20)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def fastgpt_configured(self) -> bool:
        return bool(self.fastgpt_base_url and self.fastgpt_api_key and self.fastgpt_app_id)

    @property
    def fastgpt_chat_url(self) -> str | None:
        if not self.fastgpt_base_url:
            return None
        base = self.fastgpt_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/api/v1"):
            return f"{base}/chat/completions"
        if base.endswith("/api"):
            return f"{base}/v1/chat/completions"
        return f"{base}/api/v1/chat/completions"

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def auth_configured(self) -> bool:
        return bool(self.database_url and self.session_secret and len(self.session_secret) >= 32)

    @property
    def secure_cookies(self) -> bool:
        return self.environment.lower() not in {"development", "test", "testing"}

    @property
    def memory_configured(self) -> bool:
        return bool(
            self.memory_enabled
            and self.mem0_llm_api_key
            and self.mem0_embedder_api_key
            and self.mem0_postgres_url
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

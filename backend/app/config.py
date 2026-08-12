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


@lru_cache
def get_settings() -> Settings:
    return Settings()

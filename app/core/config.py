from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Virexa API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    session_cookie_name: str = "virexa_session"
    session_ttl_seconds: int = Field(default=60 * 60 * 8, ge=60, le=86400)
    allowed_origins: str = "http://localhost:3000"

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_origins(self):
        if not self.origins or "*" in self.origins:
            raise ValueError("Explicit trusted origins are required")
        if self.environment == "production" and any(
            not origin.startswith("https://") for origin in self.origins
        ):
            raise ValueError("Production origins require HTTPS")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

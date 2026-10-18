from functools import lru_cache

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    allow_legacy_registration: bool = False
    signup_email_delivery: str = "disabled"
    signup_smtp_port: int = Field(default=587, ge=1, le=65535)
    signup_smtp_host: str | None = None
    signup_smtp_sender: EmailStr | None = None
    signup_smtp_username: str | None = None
    signup_smtp_password: SecretStr | None = None
    app_name: str = "Virexa API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str
    document_scanner_socket: str | None = None
    document_storage_root: str | None = None
    public_api_origin: str = "http://localhost:3000"
    redis_url: str = "redis://localhost:6379/0"
    session_cookie_name: str = "virexa_session"
    session_ttl_seconds: int = Field(default=60 * 60 * 8, ge=60, le=86400)
    allowed_origins: str = "http://localhost:3000"

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_origins(self):
        if self.allow_legacy_registration and self.environment != "test":
            raise ValueError("Legacy registration is test-only")
        if self.signup_email_delivery in {"capture", "local_smtp"} and self.environment != "test":
            raise ValueError("Synthetic email adapters are test-only")
        if self.signup_email_delivery not in {"disabled", "capture", "local_smtp", "smtp"}:
            raise ValueError("Unsupported signup email adapter")
        if self.signup_email_delivery == "smtp":
            if not self.signup_smtp_host or not self.signup_smtp_sender:
                raise ValueError("SMTP host and sender must be explicitly configured")
            if bool(self.signup_smtp_username) != bool(self.signup_smtp_password):
                raise ValueError("Configure both SMTP username and password, or neither")
        if not self.origins or "*" in self.origins:
            raise ValueError("Explicit trusted origins are required")
        if self.environment == "production" and any(
            not origin.startswith("https://") for origin in self.origins
        ):
            raise ValueError("Production origins require HTTPS")
        if self.document_storage_root and self.public_api_origin not in self.origins:
            raise ValueError("Session-bound upload origin must match a trusted frontend origin")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

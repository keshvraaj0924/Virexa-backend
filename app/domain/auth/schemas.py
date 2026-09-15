from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    organization_name: str = Field(min_length=2, max_length=160)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if value.lower() in {"password123456", "qwerty123456", "virexa123456"}:
            raise ValueError("Password is too common")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserSummary(BaseModel):
    id: str
    display_name: str
    email: EmailStr
    role: str


class SessionResponse(BaseModel):
    authenticated: bool
    user: UserSummary | None = None
    organization_id: str | None = None

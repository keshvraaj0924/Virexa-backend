"""Single-use mailbox proof. No authenticated identity exists before completion."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import DateTime, Integer, String, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

from app.core.authentication import AuthenticationService
from app.core.errors import ApiError
from app.db.base import Base
from app.db.models import Organization, User
from app.domain.auth.schemas import RegisterRequest
from app.domain.auth.service import issue_session, session_payload


class SignupChallenge(Base):
    __tablename__ = "signup_challenges"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    organization_name: Mapped[str | None] = mapped_column(String(160))
    account_type: Mapped[str] = mapped_column(String(16))
    code_digest: Mapped[str | None] = mapped_column(String(64))
    setup_digest: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class StartSignup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_type: Literal["individual", "organization"] = Field(alias="accountType")
    display_name: str = Field(alias="displayName", min_length=2, max_length=120)
    email: EmailStr
    organization_name: str | None = Field(default=None, alias="organizationName", max_length=160)

    @model_validator(mode="after")
    def names(self):
        self.display_name = self.display_name.strip()
        if len(self.display_name) < 2:
            raise ValueError("Name required")
        if self.account_type == "organization":
            if not self.organization_name or len(self.organization_name.strip()) < 2:
                raise ValueError("Organization name required")
            self.organization_name = self.organization_name.strip()
        elif self.organization_name is not None:
            raise ValueError("Individual accounts do not have an organization name")
        return self


class VerifySignup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID = Field(alias="challengeId")
    code: str = Field(min_length=1, max_length=128)


class CompleteSignup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID = Field(alias="challengeId")
    setup_token: str = Field(alias="setupToken", min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def strong_password(self):
        RegisterRequest.validate_password(self.password)
        normalized = self.password.casefold().strip()
        if (
            len(normalized) < 12
            or len(set(normalized)) < 4
            or normalized.isdigit()
            or normalized
            in {
                "qwertyuiopasdf",
                "abcdefghijkl",
                "abcdefghijklmnop",
                "password123456789",
                "letmein123456",
                "welcome123456",
                "123456abcdef",
                "changeme12345",
            }
            or any(
                normalized == normalized[:size] * (len(normalized) // size)
                for size in range(1, len(normalized) // 2 + 1)
                if len(normalized) % size == 0
            )
        ):
            raise ValueError("Use a unique password or a long passphrase")
        return self


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def invalid():
    return ApiError(400, "SIGNUP_PROOF_INVALID", "Verification expired or invalid. Start again.")


class UnavailableDelivery:
    def ready(self):
        raise ApiError(
            503, "EMAIL_DELIVERY_UNAVAILABLE", "Email verification is unavailable. Try later."
        )

    async def send(self, email: str, code: str):
        self.ready()


class SyntheticCaptureDelivery:
    """Test-only sink; never installed outside explicit test mode, never logged."""

    def __init__(self):
        self.messages: dict[str, str] = {}

    def ready(self):
        if len(self.messages) >= 1000:
            raise ApiError(503, "EMAIL_DELIVERY_UNAVAILABLE", "Email verification is unavailable.")

    async def send(self, email: str, code: str):
        self.messages[email] = code


async def start(db: AsyncSession, data: StartSignup, delivery):
    delivery.ready()  # Global failure, independent of whether this email exists.
    email = str(data.email).lower()
    # Serialize first INSERT as well as resend on PostgreSQL; no client-provided tenant keys.
    if db.bind.dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(email.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    row = await db.scalar(
        select(SignupChallenge)
        .where(SignupChallenge.email == email)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(timezone.utc)
    if row and utc(row.sent_at) > now - timedelta(seconds=60):
        raise ApiError(429, "SIGNUP_COOLDOWN", "Wait before requesting another code.")
    code = secrets.token_urlsafe(24)
    if row is None:
        row = SignupChallenge(email=email)
        db.add(row)
    row.id = uuid4()  # Invalidates every previous challenge handle and setup token.
    row.display_name = data.display_name
    row.organization_name = data.organization_name
    row.account_type = data.account_type
    row.code_digest = digest(code)
    row.setup_digest = None
    row.expires_at = now + timedelta(minutes=10)
    row.sent_at = now
    row.attempts = 0
    await db.flush()
    try:
        await delivery.send(email, code)
    except Exception:
        await db.rollback()
        raise ApiError(
            503, "EMAIL_DELIVERY_UNAVAILABLE", "Email verification is unavailable."
        ) from None
    await db.commit()
    return {
        "challengeId": str(row.id),
        "expiresInSeconds": 600,
        "resendAfterSeconds": 60,
        "message": "Check your email for a verification code.",
    }


async def locked(db: AsyncSession, challenge_id: UUID):
    return await db.scalar(
        select(SignupChallenge)
        .where(SignupChallenge.id == challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def verify(db: AsyncSession, data: VerifySignup):
    row = await locked(db, data.challenge_id)
    if (
        not row
        or not row.code_digest
        or row.attempts >= 5
        or utc(row.expires_at) <= datetime.now(timezone.utc)
    ):
        raise invalid()
    row.attempts += 1
    if not compare_digest(row.code_digest, digest(data.code)):
        await db.commit()  # Failed attempts must survive the error response.
        raise invalid()
    token = secrets.token_urlsafe(32)
    row.code_digest = None
    row.setup_digest = digest(token)
    # Password proof never outlives the original verification window.
    await db.commit()
    return {
        "setupToken": token,
        "expiresInSeconds": max(
            0, int((utc(row.expires_at) - datetime.now(timezone.utc)).total_seconds())
        ),
    }


async def complete(db: AsyncSession, data: CompleteSignup, ttl: int):
    row = await locked(db, data.challenge_id)
    if (
        not row
        or not row.setup_digest
        or utc(row.expires_at) <= datetime.now(timezone.utc)
        or not compare_digest(row.setup_digest, digest(data.setup_token))
    ):
        raise invalid()
    # Case-insensitive check matches existing legacy authentication behavior.
    from sqlalchemy import func

    if await db.scalar(select(User.id).where(func.lower(User.email) == row.email)):
        row.setup_digest = None
        await db.commit()
        raise invalid()
    password_hash = await run_in_threadpool(AuthenticationService.hash_password, data.password)
    if utc(row.expires_at) <= datetime.now(timezone.utc):
        raise invalid()
    organization = Organization(
        name=row.organization_name or row.display_name, account_type=row.account_type
    )
    db.add(organization)
    await db.flush()
    user = User(
        organization_id=organization.id,
        display_name=row.display_name,
        email=row.email,
        password_hash=password_hash,
        role="organization_admin",
    )
    db.add(user)
    await db.flush()
    token, session = await issue_session(db, user, ttl)
    row.setup_digest = None
    await db.commit()
    return token, session_payload(user, organization, session)


class LocalSmtpDelivery:
    """Loopback-only synthetic SMTP sink, selectable only in test mode."""

    def __init__(self, port: int):
        self.port = port

    def ready(self):
        pass

    async def send(self, email: str, code: str):
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["From"] = "virexa@example.invalid"
        message["To"] = email
        message["Subject"] = "Your Virexa verification code"
        message.set_content(
            f"Your verification code: {code}\nExpires in 10 minutes. Never share this code."
        )

        def deliver():
            with smtplib.SMTP("127.0.0.1", self.port, timeout=5) as smtp:
                smtp.send_message(message)

        await run_in_threadpool(deliver)


class SmtpDelivery:
    """Explicit server configuration only. TLS and certificate validation required."""

    def __init__(self, settings):
        self.settings = settings

    def ready(self):
        if not self.settings.signup_smtp_host or not self.settings.signup_smtp_sender:
            UnavailableDelivery().ready()

    async def send(self, email: str, code: str):
        import smtplib
        import ssl
        from email.message import EmailMessage

        message = EmailMessage()
        message["From"] = str(self.settings.signup_smtp_sender)
        message["To"] = email
        message["Subject"] = "Your Virexa verification code"
        message.set_content(
            f"Your verification code: {code}\nExpires in 10 minutes. Never share this code."
        )

        def deliver():
            with smtplib.SMTP(
                self.settings.signup_smtp_host, self.settings.signup_smtp_port, timeout=5
            ) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if self.settings.signup_smtp_username:
                    smtp.login(
                        self.settings.signup_smtp_username,
                        self.settings.signup_smtp_password.get_secret_value(),
                    )
                refused = smtp.send_message(message)
                if refused:
                    raise RuntimeError("Delivery rejected")

        await run_in_threadpool(deliver)

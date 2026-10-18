"""Durable internal metering. Never accept these records from an untrusted client."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubscriptionPeriod(Base):
    __tablename__ = "usage_periods"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_usage_period_tenant"),
        UniqueConstraint("organization_id", "starts_at", name="uq_usage_period"),
        CheckConstraint("ends_at > starts_at", name="ck_usage_period_bounds"),
        CheckConstraint("upload_limit >= 0", name="ck_usage_upload_limit"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    plan: Mapped[str] = mapped_column(String(64), default="internal_preview")
    status: Mapped[str] = mapped_column(String(24), default="preview")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    upload_limit: Mapped[int] = mapped_column(Integer, default=1000)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_usage_user_tenant",
        ),
        ForeignKeyConstraint(
            ["period_id", "organization_id"],
            ["usage_periods.id", "usage_periods.organization_id"],
            name="fk_usage_period_tenant",
        ),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_usage_event_key"),
        CheckConstraint("quantity >= 0", name="ck_usage_quantity"),
        CheckConstraint(
            "state IN ('reserved','succeeded','failed','released')", name="ck_usage_state"
        ),
        CheckConstraint(
            "meter IN ('document_uploads','ocr_pages','workflow_runs','input_tokens','output_tokens','cached_tokens')",
            name="ck_usage_meter",
        ),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_usage_amount"),
        CheckConstraint(
            "(amount IS NULL AND currency IS NULL AND pricing_version IS NULL) OR (amount IS NOT NULL AND currency IS NOT NULL AND pricing_version IS NOT NULL)",
            name="ck_usage_pricing",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    period_id: Mapped[UUID] = mapped_column(ForeignKey("usage_periods.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(64))
    meter: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    currency: Mapped[str | None] = mapped_column(String(3))
    pricing_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

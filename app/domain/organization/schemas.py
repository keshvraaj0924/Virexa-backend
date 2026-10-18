"""Bounded, existing-member administration; never account creation or invitations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TenantRole = Literal[
    "tenant_admin", "organization_admin", "manager", "operator", "viewer", "auditor"
]


class MemberQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)


class ChangeRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: TenantRole
    expectedVersion: int = Field(strict=True, gt=0)

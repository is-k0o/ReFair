"""Project and actor domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TenantKnowledge(StrEnum):
    UNKNOWN = "UNKNOWN"
    KNOWN = "KNOWN"


class Actor(BaseModel):
    """An isolated browser and authentication context."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    firefox_profile: str = Field(min_length=1)
    listener: int = Field(ge=1, le=65535)
    tenant_knowledge: TenantKnowledge = TenantKnowledge.UNKNOWN
    tenant_id: str | None = None

    @model_validator(mode="after")
    def tenant_fields_are_consistent(self) -> Actor:
        if self.tenant_knowledge is TenantKnowledge.UNKNOWN and self.tenant_id is not None:
            raise ValueError("tenant_id requires tenant_knowledge=KNOWN")
        if self.tenant_knowledge is TenantKnowledge.KNOWN and not self.tenant_id:
            raise ValueError("tenant_knowledge=KNOWN requires tenant_id")
        return self


class Project(BaseModel):
    """One isolated, authorized research target."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    actors: tuple[Actor, ...] = ()
    target_scope_reference: str | None = None
    current_run_id: UUID | None = None

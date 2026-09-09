"""Evidence and interpretations derived from evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from refair.models.project import utc_now


class ObservationProvenance(StrEnum):
    BROWSER = "BROWSER"
    HUMAN_REPEATER = "HUMAN_REPEATER"
    AGENT_REPLAY = "AGENT_REPLAY"


class Observation(BaseModel):
    """Immutable raw request/response evidence."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    observed_at: datetime = Field(default_factory=utc_now)
    provenance: ObservationProvenance
    actor_id: str | None = None
    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    response_status: int | None = Field(default=None, ge=100, le=599)
    raw_request: bytes
    raw_response: bytes | None = None


class EntityClaim(BaseModel):
    """A provenance-bearing assertion about an entity."""

    model_config = ConfigDict(frozen=True)

    attribute: str = Field(min_length=1)
    value: Any
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    project_id: UUID
    kind: str = Field(min_length=1)
    claims: tuple[EntityClaim, ...] = ()


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    INFERRED = "INFERRED"
    TESTED = "TESTED"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    statement: str = Field(min_length=1)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    supporting_evidence_ids: tuple[UUID, ...] = ()
    contradicting_evidence_ids: tuple[UUID, ...] = ()

    @field_validator("contradicting_evidence_ids")
    @classmethod
    def evidence_sets_must_not_overlap(
        cls, value: tuple[UUID, ...], info: Any
    ) -> tuple[UUID, ...]:
        supporting = set(info.data.get("supporting_evidence_ids", ()))
        if supporting.intersection(value):
            raise ValueError("evidence cannot both support and contradict a hypothesis")
        return value

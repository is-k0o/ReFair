"""Proposed experiments and deterministic policy outcomes."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ExperimentStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Experiment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    hypothesis_id: UUID
    actor_id: str = Field(min_length=1)
    baseline_evidence_id: UUID
    minimal_mutation: str = Field(min_length=1)
    expected_secure_outcome: str = Field(min_length=1)
    expected_interesting_outcome: str = Field(min_length=1)
    status: ExperimentStatus = ExperimentStatus.PROPOSED


class ExperimentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    observation_ids: tuple[UUID, ...] = Field(min_length=1)
    summary: str = Field(min_length=1)


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN_APPROVAL = "REQUIRE_HUMAN_APPROVAL"
    DEFER = "DEFER"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: PolicyOutcome
    reason_code: str = Field(min_length=1)
    explanation: str = Field(min_length=1)

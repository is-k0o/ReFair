"""In-memory planner decision contracts."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from refair.models.analytic import GroundingReference

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_NonEmptyText = Annotated[str, Field(min_length=1)]


class ExistingOperationTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["EXISTING_OPERATION"] = "EXISTING_OPERATION"
    operation_id: UUID


class ExactEndpointTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["EXACT_ENDPOINT"] = "EXACT_ENDPOINT"
    endpoint_id: UUID


class AssetReferencedUrlTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["ASSET_REFERENCED_URL"] = "ASSET_REFERENCED_URL"
    asset_content_hash: str = Field(pattern=_SHA256_PATTERN)
    url: str = Field(min_length=1)


PlannerTarget = Annotated[
    ExistingOperationTarget | ExactEndpointTarget | AssetReferencedUrlTarget,
    Field(discriminator="kind"),
]


class ExperimentProposal(BaseModel):
    """Planner suggestion to test a hypothesis against a known operation."""

    model_config = ConfigDict(frozen=True)

    decision_type: Literal["EXPERIMENT"] = "EXPERIMENT"
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    hypothesis_id: UUID
    actor_id: str = Field(min_length=1)
    baseline_observation_id: UUID
    target: ExistingOperationTarget
    rationale: str = Field(min_length=1)
    intended_change: str = Field(min_length=1)
    expected_secure_outcome: str = Field(min_length=1)
    expected_interesting_outcome: str = Field(min_length=1)
    grounding: tuple[GroundingReference, ...] = Field(min_length=1)


class ExplorationProposal(BaseModel):
    """Planner suggestion to reduce one specific structural uncertainty."""

    model_config = ConfigDict(frozen=True)

    decision_type: Literal["EXPLORATION"] = "EXPLORATION"
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    lead_id: UUID
    target: PlannerTarget
    requested_actor_id: str | None = Field(default=None, min_length=1)
    rationale: str = Field(min_length=1)
    requested_request_cap: int = Field(default=1, ge=1)


class WaitDecision(BaseModel):
    """Successful planner outcome when no justified useful action is available."""

    model_config = ConfigDict(frozen=True)

    decision_type: Literal["WAIT"] = "WAIT"
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    rationale: str = Field(min_length=1)
    missing_information: tuple[_NonEmptyText, ...] = ()
    related_hypothesis_ids: tuple[UUID, ...] = ()
    related_lead_ids: tuple[UUID, ...] = ()


PlannerDecision = Annotated[
    ExperimentProposal | ExplorationProposal | WaitDecision,
    Field(discriminator="decision_type"),
]

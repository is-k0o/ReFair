"""In-memory analytic contracts grounded in existing ReFair facts."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ObservationGrounding(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["OBSERVATION"] = "OBSERVATION"
    observation_id: UUID


class AssetGrounding(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["ASSET"] = "ASSET"
    content_hash: str = Field(pattern=_SHA256_PATTERN)


class ExactEndpointGrounding(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["EXACT_ENDPOINT"] = "EXACT_ENDPOINT"
    endpoint_id: UUID


class HttpOperationGrounding(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["HTTP_OPERATION"] = "HTTP_OPERATION"
    operation_id: UUID


GroundingReference = Annotated[
    ObservationGrounding
    | AssetGrounding
    | ExactEndpointGrounding
    | HttpOperationGrounding,
    Field(discriminator="kind"),
]


class ExplorationLead(BaseModel):
    """A grounded uncertainty worth investigating, not a vulnerability claim."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    question: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    grounding: tuple[GroundingReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def has_immutable_evidence_anchor(self) -> Self:
        if not any(
            isinstance(item, (ObservationGrounding, AssetGrounding))
            for item in self.grounding
        ):
            raise ValueError(
                "exploration lead grounding requires an observation or asset anchor"
            )
        return self

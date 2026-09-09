"""Minimal deterministic structural application models."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from refair.models.evidence import ObservationProvenance
from refair.models.normalized import BodyKind


class ExactEndpoint(BaseModel):
    """An exact observed HTTP surface, independent of method and query."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    project_id: UUID
    scheme: str
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str


class HttpOperation(BaseModel):
    """An exact observed method on one ExactEndpoint."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    endpoint_id: UUID
    method: str = Field(min_length=1)


class MethodAdvertisementSource(StrEnum):
    ALLOW_HEADER = "ALLOW_HEADER"
    CORS_ALLOW_METHODS = "CORS_ALLOW_METHODS"


class MethodAdvertisement(BaseModel):
    """An evidence-backed server advertisement, not verified capability."""

    model_config = ConfigDict(frozen=True)

    endpoint_id: UUID
    source: MethodAdvertisementSource
    advertised_method: str = Field(min_length=1)
    observation_id: UUID


class OperationQueryShape(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_present: bool
    query_parameter_names: tuple[str, ...]
    observation_count: int = Field(ge=1)


class RequestRepresentation(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_type: str | None
    body_kind: BodyKind
    observation_count: int = Field(ge=1)


class ResponseRepresentation(BaseModel):
    model_config = ConfigDict(frozen=True)

    response_status: int | None = Field(default=None, ge=100, le=599)
    content_type: str | None
    body_kind: BodyKind
    observation_count: int = Field(ge=1)


class ActorOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_id: str | None
    provenance: ObservationProvenance
    response_status: int | None = Field(default=None, ge=100, le=599)
    observation_count: int = Field(ge=1)

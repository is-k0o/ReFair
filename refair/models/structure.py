"""Minimal deterministic structural application models."""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class JsonDirection(StrEnum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class JsonType(StrEnum):
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"


class JsonParseStatus(StrEnum):
    PARSED = "PARSED"
    MALFORMED = "MALFORMED"
    SKIPPED_TOO_LARGE = "SKIPPED_TOO_LARGE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"


class JsonArrayItem(Enum):
    """Typed JSON path segment representing any array element."""

    ITEM = "ARRAY_ITEM"


JsonPath = tuple[str | JsonArrayItem, ...]


class JsonDocument(BaseModel):
    """Value-free parse result for one JSON request or response body."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: JsonDirection
    parse_status: JsonParseStatus
    root_type: JsonType | None = None

    @model_validator(mode="after")
    def root_type_matches_parse_status(self) -> Self:
        if (self.parse_status is JsonParseStatus.PARSED) != (
            self.root_type is not None
        ):
            raise ValueError("root type must be present exactly when JSON was parsed")
        return self


class JsonFieldObservation(BaseModel):
    """One evidence-backed JSON path/type fact without its scalar value."""

    model_config = ConfigDict(frozen=True)

    observation_id: UUID
    direction: JsonDirection
    path: JsonPath = Field(min_length=1)
    json_type: JsonType
    duplicate_key_observed: bool = False


class OperationJsonDocumentOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: JsonDirection
    parse_status: JsonParseStatus
    root_type: JsonType | None = None
    observation_count: int = Field(ge=1)


class OperationJsonField(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: JsonDirection
    path: JsonPath = Field(min_length=1)
    json_type: JsonType
    observation_count: int = Field(ge=1)
    duplicate_key_observed: bool = False


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
